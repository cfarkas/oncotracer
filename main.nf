nextflow.enable.dsl = 2

// Launch a foreground controller, not a cached sequencing task. The bundled
// GLIMPSE2 workflow schedules analysis tasks and validates reusable outputs.
// All values travel as literal argv elements; no user input is shell code.
def selectedFlag(value, name) {
    if (value instanceof Boolean) return value
    if (value == null) return false
    if (value.toString().toLowerCase() == 'true') return true
    if (value.toString().toLowerCase() == 'false') return false
    throw new IllegalArgumentException("--${name} must be true or false")
}

workflow {
    def argv = [params.python.toString(), '-m', 'oncotracer_cli.nextflow_gateway',
                '--project-dir', projectDir.toString(), '--launch-dir', launchDir.toString()]
    if (selectedFlag(params.help, 'help')) {
        argv.add('--help')
    } else {
        argv.addAll(['--analysis', params.analysis.toString(), '--port', params.port.toString()])
        if (selectedFlag(params.interactive, 'interactive')) argv.add('--interactive')
        if (selectedFlag(params.dry_run, 'dry_run')) argv.add('--dry-run')
        ['config', 'start_dir', 'backend', 'threads', 'parallel_samples'].each { key ->
            if (params[key] != null && params[key].toString() != '') {
                argv.addAll(['--' + key.replace('_', '-'), params[key].toString()])
            }
        }
    }
    def controller = new ProcessBuilder(argv)
    controller.directory(launchDir.toFile())
    controller.inheritIO()
    def previousPythonPath = controller.environment().get('PYTHONPATH')
    controller.environment().put('PYTHONPATH', projectDir.toString() +
        (previousPythonPath ? File.pathSeparator + previousPythonPath : ''))
    controller.environment().put('PYTHONUNBUFFERED', '1')
    def child = controller.start()
    def exitStatus = child.waitFor()
    if (exitStatus != 0) {
        throw new IllegalStateException("OncoTracer exited with status ${exitStatus}; inspect the preceding error.")
    }
}
