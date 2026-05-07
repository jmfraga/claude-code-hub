import asyncio
import time
import uuid
from pathlib import Path

_JOBS: dict[str, "Job"] = {}


class Job:
    def __init__(self, project: str, cmd_id: str, cmd: list[str], cwd: Path):
        self.id = uuid.uuid4().hex[:12]
        self.project = project
        self.cmd_id = cmd_id
        self.cmd = cmd
        self.cwd = cwd
        self.lines: list[str] = []
        self.done = False
        self.exit_code: int | None = None
        self.started = time.time()
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def run(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.cmd,
                cwd=str(self.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                self.lines.append(line)
                await self.queue.put(line)
            self.exit_code = await proc.wait()
        except Exception as e:
            self.lines.append(f"[error] {e}")
            await self.queue.put(f"[error] {e}")
            self.exit_code = -1
        finally:
            self.done = True
            await self.queue.put(None)


def submit(project: str, cmd_id: str, cmd: list[str], cwd: Path) -> Job:
    job = Job(project, cmd_id, cmd, cwd)
    _JOBS[job.id] = job
    asyncio.create_task(job.run())
    return job


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def list_recent(limit: int = 20) -> list[dict]:
    items = sorted(_JOBS.values(), key=lambda j: j.started, reverse=True)[:limit]
    return [
        {
            "id": j.id,
            "project": j.project,
            "cmd_id": j.cmd_id,
            "done": j.done,
            "exit_code": j.exit_code,
            "started": j.started,
        }
        for j in items
    ]
