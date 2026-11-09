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
    // -m puts launchDir ahead of PYTHONPATH. Pin the selected revision before
    // importing OncoTracer, even when launched inside an older source checkout.
    def bootstrap = 'import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); ' +
                    'runpy.run_module("oncotracer_cli.nextflow_gateway",run_name="__main__",alter_sys=True)'
    def argv = [params.python.toString(), '-c', bootstrap, projectDir.toString(),
                '--project-dir', projectDir.toString(), '--launch-dir', launchDir.toString()]
    if (selectedFlag(params.help, 'help')) {
        argv.add('--help')
    } else {
        argv.addAll(['--analysis', params.analysis.toString()])
        if (params.port != null) argv.addAll(['--port', params.port.toString()])
        if (selectedFlag(params.interactive, 'interactive')) argv.add('--interactive')
        if (selectedFlag(params.ffpe, 'ffpe')) argv.add('--ffpe')
        // Nextflow normalizes a hyphenated CLI parameter to camelCase.
        def namedOntAlias = params.containsKey('ontNamedSamples') ? params.ontNamedSamples : false
        if (selectedFlag(params.ont_named_samples, 'ont_named_samples') ||
            selectedFlag(namedOntAlias, 'ont-named-samples')) argv.add('--ont-named-samples')
        if (selectedFlag(params.no_browser, 'no_browser')) argv.add('--no-browser')
        if (selectedFlag(params.dry_run, 'dry_run')) argv.add('--dry-run')
        if (selectedFlag(params.gpu, 'gpu')) argv.add('--gpu')
        if (selectedFlag(params.cpu, 'cpu')) argv.add('--cpu')
        def mergeVcfs = params.containsKey('mergeVcfs') ? params.mergeVcfs : params.merge_vcfs
        def noMergeVcfs = params.containsKey('noMergeVcfs') ? params.noMergeVcfs : params.no_merge_vcfs
        if (selectedFlag(noMergeVcfs, 'no_merge_vcfs')) {
            if (mergeVcfs != null) throw new IllegalArgumentException('Choose either --merge_vcfs or --no_merge_vcfs')
            argv.add('--no-merge-vcfs')
        } else if (mergeVcfs != null) {
            argv.add(selectedFlag(mergeVcfs, 'merge_vcfs') ? '--merge-vcfs' : '--no-merge-vcfs')
        }
        def cnaProfile = params.containsKey('cnaProfile') ? params.cnaProfile : params.cna_profile
        if (cnaProfile != null) argv.addAll(['--cna-profile', cnaProfile.toString()])
        def storageMode = params.containsKey('storageMode') ? params.storageMode : params.storage_mode
        if (storageMode != null) argv.addAll(['--storage-mode', storageMode.toString()])
        def resourceMode = params.containsKey('resourceMode') ? params.resourceMode : params.resource_mode
        if (resourceMode != null) argv.addAll(['--resource-mode', resourceMode.toString()])
        def gpuWaitHours = params.containsKey('gpuWaitHours') ? params.gpuWaitHours : params.gpu_wait_hours
        if (gpuWaitHours != null) argv.addAll(['--gpu-wait-hours', gpuWaitHours.toString()])
        ['config', 'start_dir', 'backend', 'threads', 'parallel_samples', 'project', 'input_folder',
         'cpg_from', 'classifier', 'resources',
         'image', 'variant_tool_prefix', 'variant_ffperase_root', 'variant_ffperase_models',
         'variant_ffperase_sif', 'variant_ffperase_prefix'].each { key ->
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
