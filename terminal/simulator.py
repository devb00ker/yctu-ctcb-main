"""仅本机联调：真实 MQTT broker、Protobuf 和 HEVC/UDP，状态为演示数据。"""
import argparse
import asyncio
import json
import logging
import math
import signal
import socket
import threading
import time
from fractions import Fraction
from pathlib import Path
import av
import numpy as np
import paho.mqtt.client as mqtt
from amqtt.broker import Broker
from .protocol import UP, encode, decode
from .video import fragment


class LocalBroker(Broker):
    async def _shutdown_broadcast_loop(self):
        # amqtt 0.11.3：关闭 session 会取消 QoS waiter，其取消会传播到广播任务。
        # 只接纳子任务的正常取消，继续完成服务 socket 清理；外部取消仍向上传播。
        try:
            await super()._shutdown_broadcast_loop()
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise


def samples(t=0):
    return {
        "GameStatus": dict(current_round=1, total_rounds=3, red_score=1, blue_score=0,
                           current_stage=4, stage_countdown_sec=max(0, 300-int(t) % 300),
                           stage_elapsed_sec=int(t) % 300, is_paused=False, game_result=255, end_reason=255),
        "GlobalUnitStatus": dict(base_health=2000, base_status=1, base_shield=1,
                                 enemy_base_health=1800, enemy_base_status=1, enemy_base_shield=1,
                                 robot_health=[500, 380, 400, 600, 450, 280, 350, 600],
                                 robot_bullets=[30, 150, 150, 300], total_damage_ally=1200,
                                 total_damage_enemy=900, total_CP_ally=35, total_CP_enemy=22),
        "GlobalLogisticsStatus": dict(remaining_economy=900, total_economy_obtained="1600", tech_level=1, encryption_level=1),
        "GlobalSpecialMechanism": dict(mechanism_id=[3], mechanism_time_sec=[-5]),
        "RobotStaticStatus": dict(connection_state=1, field_state=0, alive_state=1, robot_id=3,
                                  robot_type=3, performance_system_shooter=1, performance_system_chassis=1,
                                  level=2, max_health=400, max_heat=200, heat_cooldown_rate=40,
                                  max_power=80, max_buffer_energy=60),
        "RobotDynamicStatus": dict(current_health=380, current_heat=round(60+40*math.sin(t), 1),
                                   last_projectile_fire_rate=20, current_buffer_energy=50,
                                   current_experience=100, experience_for_upgrade=200,
                                   remaining_ammo=150, total_projectiles_fired=int(t)*2,
                                   is_out_of_combat=True, can_remote_heal=True, can_remote_ammo=True),
        "RobotModuleStatus": dict(power_manager=1, rfid=1, light_strip=1, small_shooter=1,
                                  big_shooter=0, uwb=1, armor=1, video_transmission=1, capacitor=1,
                                  main_controller=1, modular_42mm=0),
        "RobotPosition": dict(x=4+math.sin(t/3), y=3+math.cos(t/3), z=0, yaw=t*10 % 360, robot_id=3),
        "RobotInjuryStat": dict(total_damage=20, small_projectile_damage=20),
        "RobotRespawnStatus": dict(is_pending_respawn=False, total_respawn_progress=10,
                                   current_respawn_progress=0, can_free_respawn=False,
                                   can_pay_for_respawn=False, gold_cost_for_respawn=100),
        "Buff": dict(robot_id=3, buff_type=3, buff_level=20, buff_max_time=30, buff_left_time=20),
        "PenaltyInfo": dict(penalty_type=1, penalty_effect_sec=0, total_penalty_num=0),
        "Event": dict(event_id=1, param="103,3"),
        "RobotPathPlanInfo": dict(intention=3, start_pos_x=20, start_pos_y=20,
                                  offset_x=list(range(1,50)), offset_y=[0]*49, sender_id=7),
        "MapClickInfo": dict(is_send_all=0, robot_id="AwAAAAAAAA==", mode=3, type=1, map_x=4, map_y=3),
        "RadarInfoToClient": {"radar_single_robot_info": [dict(target_pos_x=100+i*100,
                                     target_pos_y=100+(i%3)*150, is_high_light=i%3) for i in range(8)]},
        "CustomByteBlock": {"data": "WUNUVQ=="},
        "TechCoreMotionStateSync": dict(maximum_difficulty_level=4, basic_state=1, putin_state=0, move_state=0, rotate_state=0),
        "RobotPerformanceSelectionSync": dict(shooter=1, chassis=1, sentry_control=0),
        "RuneStatusSync": dict(rune_status=1, activated_arms=0, average_rings=0),
        "SentryStatusSync": dict(posture_id=3, is_weakened=False, is_powered=False),
        "DartSelectTargetStatusSync": dict(target_id=1, open=0),
        "SentryCtrlResult": dict(command_id=9, result_code=0),
    }


class Simulator:
    def __init__(self, port=3333, video_port=3334, width=960, height=540, fps=30, video=True, record=None):
        self.port, self.video_port = port, video_port
        self.width, self.height, self.fps = width, height, fps
        self.video = video
        self.record = Path(record) if record else None
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.commands = []
        self.overrides = {}
        self.error = None
        self.threads = []
        self.client = None

    def start(self):
        self.threads = [threading.Thread(target=self._broker_thread, daemon=True, name="sim-broker")]
        self.threads[0].start()
        if not self.ready.wait(8) or self.error:
            raise RuntimeError(f"模拟 broker 启动失败：{self.error}")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="yctu-simulator")
        connected = threading.Event()

        def on_connect(c, userdata, flags, reason, properties):
            c.subscribe([(n, 1) for n in UP])

        def on_subscribe(c, userdata, mid, reasons, properties):
            connected.set()

        def on_message(c, userdata, msg):
            try:
                data = decode(msg.topic, msg.payload)
                row = dict(time=time.time(), topic=msg.topic, data=data, hex=msg.payload.hex())
                with self.lock:
                    self.commands.append(row)
                    self.commands = self.commands[-1000:]
                    if self.record:
                        self.record.parent.mkdir(parents=True, exist_ok=True)
                        with self.record.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                if msg.topic == "SentryCtrlCommand":
                    self.publish("SentryCtrlResult", dict(command_id=data["command_id"], result_code=0))
                    if data["command_id"] >= 7:
                        self.overrides["SentryStatusSync"] = dict(posture_id=(data["command_id"]-7)%3+1,
                                                                is_powered=data["command_id"]>=10, is_weakened=False)
                elif msg.topic == "RobotPerformanceSelectionCommand":
                    self.overrides["RobotPerformanceSelectionSync"] = data
                elif msg.topic == "MapClickCmd":
                    self.publish("MapClickInfo", data)
            except Exception as exc:
                logging.warning("模拟器丢弃上行：%s", exc)

        self.client.on_connect, self.client.on_subscribe = on_connect, on_subscribe
        self.client.on_message = on_message
        self.client.connect("127.0.0.1", self.port, 10)
        self.client.loop_start()
        if not connected.wait(5):
            self.stop()
            raise TimeoutError("模拟器发布端未就绪")
        self.threads.append(threading.Thread(target=self._telemetry, daemon=True, name="sim-state"))
        if self.video:
            self.threads.append(threading.Thread(target=self._video, daemon=True, name="sim-video"))
        for thread in self.threads[1:]:
            thread.start()

    def _broker_thread(self):
        async def run():
            broker = LocalBroker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
                             "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
            await broker.start()
            self.ready.set()
            try:
                while not self.stop_event.is_set():
                    await asyncio.sleep(0.05)
            finally:
                await broker.shutdown()
        try:
            asyncio.run(run())
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()

    def publish(self, topic, data):
        if self.client and self.client.is_connected():
            self.client.publish(topic, encode(topic, data), qos=0, retain=False)

    def _telemetry(self):
        start = time.monotonic()
        count = 0
        while not self.stop_event.is_set():
            data = samples(time.monotonic()-start)
            data.update(self.overrides)
            for topic, values in data.items():
                if topic == "RobotDynamicStatus" or topic == "GameStatus" and count % 2 == 0 or count % 10 == 0:
                    self.publish(topic, values)
            count += 1
            self.stop_event.wait(0.1)

    def _video(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            codec = av.CodecContext.create("libx265", "w")
            codec.width, codec.height = self.width, self.height
            codec.pix_fmt = "yuv420p"
            codec.time_base = Fraction(1, self.fps)
            codec.framerate = Fraction(self.fps, 1)
            codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                             "x265-params": f"keyint={self.fps}:min-keyint={self.fps}:repeat-headers=1:bframes=0:log-level=error:pools=2"}
            codec.open()
            fid, frame_index = 0, 0
            x = np.arange(self.width, dtype=np.uint16)[None, :]
            y = np.arange(self.height, dtype=np.uint16)[:, None]
            while not self.stop_event.is_set():
                tick = time.monotonic()
                rgb = np.empty((self.height, self.width, 3), dtype=np.uint8)
                rgb[:, :, 0] = (x + frame_index*3) % 256
                rgb[:, :, 1] = (y + frame_index) % 256
                rgb[:, :, 2] = ((x//80+y//80) % 2)*120+40
                frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
                frame.pts = frame_index
                for packet in codec.encode(frame):
                    for datagram in fragment(bytes(packet), fid):
                        sock.sendto(datagram, ("127.0.0.1", self.video_port))
                    fid = (fid + 1) & 65535
                frame_index += 1
                self.stop_event.wait(max(0, 1/self.fps - (time.monotonic()-tick)))
        except Exception as exc:
            self.error = f"模拟图传失败：{exc}"
            logging.exception(self.error)
        finally:
            sock.close()

    def stop(self):
        self.stop_event.set()
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description="YCTU 本地模拟器（不等价于裁判系统）")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--video-port", type=int, default=3334)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--record", default="output/simulator-commands.jsonl")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    sim = Simulator(args.port, args.video_port, args.width, args.height, args.fps, not args.no_video, args.record)
    try:
        sim.start()
        print(f"本地演示 MQTT 127.0.0.1:{args.port} / UDP {args.video_port}；Ctrl+C 退出", flush=True)
        while not sim.stop_event.wait(0.5):
            if sim.error:
                raise RuntimeError(sim.error)
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
