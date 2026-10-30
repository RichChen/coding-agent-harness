"""Container RPC wrappers for all file tools; no host resolve/read/write path."""
import json
import time
from pathlib import Path

from src.execution.base import ExecutionRequest
from src.tools.base import Tool, ToolResult
from src.tools.command import RunCommandTool


FILE_CODE = r'''
import json,os,pathlib,sys,tempfile
req=json.load(sys.stdin); p=req['params']; root=pathlib.Path('/workspace')
raw=p['path']; rel=pathlib.PurePosixPath(raw)
if rel.is_absolute() or '..' in rel.parts or chr(92) in raw: raise ValueError('path denied')
path=(root/rel).resolve()
try: path.relative_to(root)
except ValueError: raise ValueError('path denied') from None
limit=req['file_limit']; name=req['name']
if name=='list_dir':
    entries=sorted(path.iterdir())
    if len(entries)>2048: raise ValueError('directory entry limit')
    print('\n'.join(('[D] ' if q.is_dir() else '[F] ')+q.name for q in entries))
else:
    # Enabled only for workspace-v1. Legacy temporary-file mode is unchanged.
    mode = ((path.stat().st_mode & 0o7777) if path.exists() else 0o644) if req.get('preserve_mode') else None
    if name=='write_file': content=p['content']
    else:
        with path.open('rb') as f: data=f.read(limit+1)
        if len(data)>limit: raise ValueError('file size limit')
        content=data.decode('utf-8')
    if name=='read_file':
        lines=content.split('\n'); total=len(lines)
        start=p.get('offset') or 1
        if not isinstance(start,int) or start<1: start=1
        limits=[n for n in (p.get('limit'),req.get('line_limit')) if isinstance(n,int) and n>0]
        end=total if not limits else min(total, start-1+min(limits))
        if start>total:
            print(f'[showing lines {start}-{start-1} of {total}; call read_file with offset=1 to continue]')
        else:
            numbered=[f'{start+i}|{line}' for i,line in enumerate(lines[start-1:end])]
            cap=req.get('char_limit')
            cut=False
            if isinstance(cap,int) and cap>0:
                kept=[]; used=0
                for line in numbered:
                    extra=len(line)+(1 if kept else 0)
                    if kept and used+extra>cap:
                        cut=True; break
                    if not kept and len(line)>cap:
                        kept.append(line[:cap]); cut=True; break
                    kept.append(line); used+=extra
                if len(kept)<len(numbered):
                    end=start+len(kept)-1
                    numbered=kept
            else:
                cut=False
            body='\n'.join(numbered)
            if end<total or cut:
                hint=f'[showing lines {start}-{end} of {total}; call read_file with offset={end+1} to continue]'
                body=(body+'\n'+hint) if body else hint
            print(body)
    else:
        if name in ('edit_file','search_replace'):
            old=p['old_string'] if name=='edit_file' else p['search']
            new=p['new_string'] if name=='edit_file' else p['replace']
            if old not in content: raise ValueError('search string not found')
            if name=='search_replace' and content.count(old)!=1: raise ValueError('search must be unique')
            content=content.replace(old,new,1)
        data=content.encode('utf-8')
        if len(data)>limit: raise ValueError('file size limit')
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
            if req.get('preserve_mode'): os.chmod(tmp,mode)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        print('file updated')
'''


class ContainerFileTool(Tool):
    def __init__(self, original, backend, *, preserve_mode=False):
        self.name, self.description = original.name, original.description
        self.parameters, self.side_effect = original.parameters, original.side_effect
        self.backend = backend
        self.preserve_mode = preserve_mode

    async def execute(self, params, ctx):
        deadline = ctx.extra.get("deadline")
        limit = ctx.file_size_limit_bytes if ctx.file_size_limit_bytes is not None else 1024*1024
        request = ExecutionRequest(argv=["/usr/local/bin/python", "-I", "-S", "-c", FILE_CODE],
            stdin_bytes=json.dumps(dict(name=self.name, params=params, file_limit=min(limit,1024*1024),
                                       line_limit=ctx.read_file_max_lines, char_limit=ctx.tool_output_max_chars,
                                       preserve_mode=self.preserve_mode)).encode(),
            env={}, cwd=ctx.workspace_root, cancel_event=ctx.extra.get("cancel_event"),
            deadline_mono=deadline.effective_deadline_mono() if deadline else time.monotonic()+30,
            run_meter=ctx.extra.get("run_output_meter"),
            artifact_dir=(Path(ctx.extra['artifact_dir']) / ctx.extra['operation_id'])
                if ctx.extra.get('artifact_dir') and ctx.extra.get('operation_id') else None)
        result = await self.backend.execute(request)
        output = result.observation_text()
        if ctx.tool_output_max_chars is not None and self.name != "read_file":
            output = output[:ctx.tool_output_max_chars]
        return ToolResult(ctx.call_id, self.name, result.status.value=="succeeded", output,
            error=result.error_message, status=RunCommandTool._map_status(result.status),
            error_kind=result.error_kind, exit_code=result.exit_code, cleanup_state=result.cleanup_state.value,
            cleanup_elapsed_s=result.cleanup_elapsed_s, truncated=result.truncated,
            bytes_seen=result.bytes_seen, bytes_kept=result.bytes_kept)
