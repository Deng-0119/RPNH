from .. import native

class NativeDriver:
    host='native'
    def run(self, *, run_dir, profile, broker, messages, schemas, control_root, stop_path=None, **unused):
        spec=native.build_spec(run_dir,profile,broker.endpoint,broker.run_id,messages,schemas)
        return native.run(spec,control_root,stop_path=stop_path)
    def project(self, run_dir, output):
        return native.project_registry(run_dir,output)
