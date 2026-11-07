"""Exec child with Linux address-space, CPU and file-size limits."""
import os
import sys

if os.name == 'posix':
    import resource
    memory = int(os.environ.get('PARSE_MEM_MB', '2048')) * 1024**2
    cpu = int(os.environ.get('PARSE_CPU_S', '240'))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    cap = int(os.environ.get('MAX_RAW_MB', '1024')) * 1024**2
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
if os.name == 'posix':
    os.execvp(sys.argv[1], sys.argv[1:])
else:
    import subprocess
    sys.exit(subprocess.call(sys.argv[1:]))
