# Protobuf dependency closure

The checked-in `app/proto/gcsdk_gcmessages_pb2.py` imports SteamLearn, which
also imports unified-base. These two generated modules were missing from
`app/proto`. They are copied unchanged (apart from LF normalization) from the
working prototype's generated Python modules, rather than regenerated or
replaced with stubs. The prototype source checkout is valveprotos-rs revision
`63620c46236ed0f03a9e8b75954a4dafd0fe8e53`, matching `prototype/VALVEPROTOS_REV`.

`process.py` adds this directory to the import path after `app/proto`, so
modules in the main vendored directory take precedence. No runtime dependency
on the prototype's temporary directories is needed.
