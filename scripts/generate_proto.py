from pathlib import Path
from grpc_tools import protoc

root = Path(__file__).resolve().parent.parent
result = protoc.main(["protoc", f"-I{root / 'proto'}", f"--python_out={root / 'terminal' / 'generated'}",
                      *[str(p) for p in sorted((root / "proto").glob("*.proto"))]])
if result:
    raise SystemExit(result)
print("Generated RM2027 protocol classes.")
