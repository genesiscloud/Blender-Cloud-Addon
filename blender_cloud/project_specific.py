"""Handle saving and loading project-specific settings."""

import logging

# Names of BlenderCloudPreferences properties that are both project-specific
# and simple enough to store directly in a dict.
PROJECT_SPECIFIC_SIMPLE_PROPS = (
    'cloud_project_local_path',
    'flamenco_exclude_filter',
    'flamenco_job_file_path',
    'flamenco_job_output_path',
    'flamenco_job_output_strip_components'
)

log = logging.getLogger(__name__)
project_settings_loading = False


def handle_project_update(_=None, _2=None):
    """Handles changing projects, which may cause extensions to be disabled/enabled.

    Ignores arguments so that it can be used as property update callback.
    """

    from .blender import preferences, project_extensions

    global project_settings_loading
    project_settings_loading = True
    try:
        prefs = preferences()
        project_id = prefs.project.project
        log.info('Updating internal state to reflect extensions enabled on current project %s.',
                 project_id)

        project_extensions.cache_clear()

        from blender_cloud import attract, flamenco
        attract.deactivate()
        flamenco.deactivate()

        enabled_for = project_extensions(project_id)
        log.info('Project extensions: %s', enabled_for)
        if 'attract' in enabled_for:
            attract.activate()
        if 'flamenco' in enabled_for:
            flamenco.activate()

        # Load project-specific settings from the last time we visited this project.
        ps = prefs.get('project_settings', {}).get(project_id, {})
        if not ps:
            log.debug('no project-specific settings are available, not touching options')
            return

        if log.isEnabledFor(logging.DEBUG):
            from pprint import pformat
            log.debug('loading project-specific settings:\n%s', pformat(ps.to_dict()))

        for name in PROJECT_SPECIFIC_SIMPLE_PROPS:
            if name in ps and hasattr(prefs, name):
                setattr(prefs, name, ps[name])
        if ps.get('flamenco_manager'):
            prefs.flamenco_manager.manager = ps['flamenco_manager']
            log.debug('setting flamenco manager to %s', ps['flamenco_manager'])

    finally:
        project_settings_loading = False


def store(_=None, _2=None):
    """Remember project-specific settings as soon as one of them changes.

    Ignores arguments so that it can be used as property update callback.

    No-op when project_settings_loading=True, to prevent saving project-
    specific settings while they are actually being loaded.
    """
    from .blender import preferences

    global project_settings_loading
    if project_settings_loading:
        return

    prefs = preferences()
    project_id = prefs.project.project
    all_settings = prefs.get('project_settings', {})
    ps = all_settings.get(project_id, {})

    for name in PROJECT_SPECIFIC_SIMPLE_PROPS:
        ps[name] = getattr(prefs, name)
    ps['flamenco_manager'] = prefs.flamenco_manager.manager

    if log.isEnabledFor(logging.DEBUG):
        from pprint import pformat
        if hasattr(ps, 'to_dict'):
            ps_to_log = ps.to_dict()
        else:
            ps_to_log = ps
        log.debug('saving project-specific settings:\n%s', pformat(ps_to_log))

    all_settings[project_id] = ps
    prefs['project_settings'] = all_settings
