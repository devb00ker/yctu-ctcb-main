"""使用 OpenCASCADE/Gmsh 读取 STEP，生成有尺寸依据的正交俯视底图。"""
import argparse
import hashlib
import json
from pathlib import Path
import gmsh
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent / "assets" / "maps")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--up-axis", choices=["auto", "x", "y", "z"], default="auto")
    parser.add_argument("--up-sign", type=int, choices=[-1,1], default=1)
    parser.add_argument("--bounds", type=float, nargs=4, metavar=("XMIN","YMIN","XMAX","YMAX"))
    parser.add_argument("--mesh-size", type=float, default=0.20)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "M")
        gmsh.open(str(args.source.resolve()))
        gmsh.model.occ.synchronize()
        bounds = np.array(gmsh.model.getBoundingBox(-1, -1))
        lengths = bounds[3:] - bounds[:3]
        up = int(np.argmin(lengths)) if args.up_axis == "auto" else "xyz".index(args.up_axis)
        axes = [a for a in range(3) if a != up]
        vertical_sign = (-1 if up == 1 else 1) * args.up_sign
        surfaces = gmsh.model.getEntities(2)
        volumes = gmsh.model.getEntities(3)
        metadata = dict(source=str(args.source.resolve()), sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
                        unit="m", bbox=bounds.tolist(), dimensions=lengths.tolist(), up_axis="xyz"[up], up_sign=args.up_sign,
                        horizontal_axis="xyz"[axes[0]], vertical_axis=("-" if vertical_sign < 0 else "+")+"xyz"[axes[1]],
                        surface_count=len(surfaces), volume_count=len(volumes),
                        width_m=float(lengths[axes[0]]), height_m=float(lengths[axes[1]]),
                        colors=sorted(set(gmsh.model.getColor(d,t) for d,t in surfaces)),
                        alignment_confirmed=False,
                        note="模型外包围盒尺寸；原点/红蓝方向与裁判坐标尚未实机校准。")
        if args.inspect_only:
            metadata["largest_volumes"] = sorted([
                dict(tag=t, name=gmsh.model.getEntityName(d,t), bbox=gmsh.model.getBoundingBox(d,t),
                     mass=gmsh.model.occ.getMass(d,t)) for d,t in volumes], key=lambda v:v["mass"], reverse=True)[:12]
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            return
        gmsh.option.setNumber("Mesh.MeshSizeMax", args.mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", args.mesh_size / 3)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(2)
        node_ids, coords, _ = gmsh.model.mesh.getNodes()
        coords = np.asarray(coords).reshape(-1,3)
        index = {int(n):i for i,n in enumerate(node_ids)}
        triangles, colors = [], []
        for dim, tag in surfaces:
            rgba = np.array(gmsh.model.getColor(dim,tag)) / 255
            if rgba[3] == 0:
                rgba = np.array([0.25,0.31,0.39,1])
            types, _, elements = gmsh.model.mesh.getElements(dim, tag)
            for kind, nodes in zip(types, elements):
                if kind != 2:
                    continue
                ids = np.fromiter((index[int(n)] for n in nodes), dtype=np.int64).reshape(-1,3)
                tris = coords[ids]
                triangles.extend(tris)
                colors.extend([rgba] * len(tris))
        tris = np.array(triangles)
        colors = np.array(colors)
        projected = tris[:,:,axes].copy()
        projected[:,:,1] *= vertical_sign
        # 从低处到高处绘制正交投影；倾斜地形通过面法向轻微明暗区分。
        cross = np.cross(tris[:,1]-tris[:,0], tris[:,2]-tris[:,0])
        area = np.abs(cross[:,up])
        keep = area > 1e-10
        tris, projected, colors, cross = tris[keep], projected[keep], colors[keep], cross[keep]
        normal = cross / np.maximum(np.linalg.norm(cross, axis=1, keepdims=True), 1e-12)
        light = 0.82+0.18*np.abs(normal[:,up])
        colors[:,:3] *= light[:,None]
        colors[:,3] = 1
        order = np.argsort(tris[:,:,up].mean(axis=1) * args.up_sign, kind="stable")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection
        x0, x1 = bounds[axes[0]], bounds[axes[0]+3]
        yvals = [bounds[axes[1]]*vertical_sign, bounds[axes[1]+3]*vertical_sign]
        y0, y1 = min(yvals), max(yvals)
        if args.bounds:
            x0, y0, x1, y1 = args.bounds
        if x1 <= x0 or y1 <= y0:
            raise ValueError("投影范围必须为正尺寸")
        width, height = x1-x0, y1-y0
        fig = plt.figure(figsize=(12, 12*height/width), dpi=150, facecolor="#122033")
        ax = fig.add_axes((0,0,1,1))
        ax.set_facecolor("#122033")
        ax.add_collection(PolyCollection(projected[order], facecolors=colors[order],
                                         edgecolors="none", antialiased=False))
        ax.set_xlim(x0,x1)
        ax.set_ylim(y0,y1)
        ax.set_aspect("equal")
        ax.axis("off")
        image_path = args.output / "rmul2027-top.png"
        fig.savefig(image_path, dpi=150, pad_inches=0)
        plt.close(fig)
        metadata.update(triangle_count=len(tris), image=image_path.name, width_m=float(width), height_m=float(height),
                        projection_origin=[float(x0),float(y0)],
                        projection_bounds=[float(x0),float(y0),float(x1),float(y1)])
        (args.output / "rmul2027-top.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    main()
