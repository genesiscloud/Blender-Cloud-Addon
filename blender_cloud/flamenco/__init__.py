# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Flamenco interface.

The preferences are managed blender.py, the rest of the Flamenco-specific stuff is here.
"""

import functools
import logging
import os
from pathlib import Path, PurePath
import typing

if "bpy" in locals():
    import importlib

    try:
        bat_interface = importlib.reload(bat_interface)
        sdk = importlib.reload(sdk)
    except NameError:
        from . import bat_interface, sdk
else:
    from . import bat_interface, sdk

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene, PropertyGroup
from bpy.props import StringProperty, EnumProperty, PointerProperty, BoolProperty, IntProperty

from .. import async_loop, pillar, project_specific
from ..utils import pyside_cache, redraw

log = logging.getLogger(__name__)

# Global flag used to determine whether panels etc. can be drawn.
flamenco_is_active = False


@pyside_cache('manager')
def available_managers(self, context):
    """Returns the list of items used by a manager-selector EnumProperty."""

    from ..blender import preferences

    mngrs = preferences().flamenco_manager.available_managers
    if not mngrs:
        return [('', 'No managers available in your Blender Cloud', '')]
    return [(p['_id'], p['name'], '') for p in mngrs]


class FlamencoManagerGroup(PropertyGroup):
    manager = EnumProperty(
        items=available_managers,
        name='Flamenco Manager',
        description='Which Flamenco Manager to use for jobs',
        update=project_specific.store,
    )

    status = EnumProperty(
        items=[
            ('NONE', 'NONE', 'We have done nothing at all yet'),
            ('IDLE', 'IDLE', 'User requested something, which is done, and we are now idle'),
            ('FETCHING', 'FETCHING', 'Fetching available Flamenco managers from Blender Cloud'),
        ],
        name='status',
        update=redraw)

    # List of managers is stored in 'available_managers' ID property,
    # because I don't know how to store a variable list of strings in a proper RNA property.
    @property
    def available_managers(self) -> list:
        return self.get('available_managers', [])

    @available_managers.setter
    def available_managers(self, new_managers):
        self['available_managers'] = new_managers


class FlamencoPollMixin:
    @classmethod
    def poll(cls, context):
        return flamenco_is_active


class FLAMENCO_OT_fmanagers(async_loop.AsyncModalOperatorMixin,
                            pillar.AuthenticatedPillarOperatorMixin,
                            FlamencoPollMixin,
                            Operator):
    """Fetches the Flamenco Managers available to the user"""
    bl_idname = 'flamenco.managers'
    bl_label = 'Fetch available Flamenco Managers'

    stop_upon_exception = True
    log = logging.getLogger('%s.FLAMENCO_OT_fmanagers' % __name__)

    @property
    def mypref(self) -> FlamencoManagerGroup:
        from ..blender import preferences

        return preferences().flamenco_manager

    async def async_execute(self, context):
        if not await self.authenticate(context):
            return

        from .sdk import Manager
        from ..pillar import pillar_call

        self.log.info('Going to fetch managers for user %s', self.user_id)

        self.mypref.status = 'FETCHING'
        managers = await pillar_call(Manager.all)

        # We need to convert to regular dicts before storing in ID properties.
        # Also don't store more properties than we need.
        as_list = [{'_id': p['_id'], 'name': p['name']} for p in managers['_items']]

        self.mypref.available_managers = as_list
        self.quit()

    def quit(self):
        self.mypref.status = 'IDLE'
        super().quit()


class FLAMENCO_OT_render(async_loop.AsyncModalOperatorMixin,
                         pillar.AuthenticatedPillarOperatorMixin,
                         FlamencoPollMixin,
                         Operator):
    """Performs a Blender render on Flamenco."""
    bl_idname = 'flamenco.render'
    bl_label = 'Render on Flamenco'
    bl_description = __doc__.rstrip('.')

    stop_upon_exception = True
    log = logging.getLogger('%s.FLAMENCO_OT_render' % __name__)

    async def async_execute(self, context):
        # Refuse to start if the file hasn't been saved. It's okay if
        # it's dirty, but we do need a filename and a location.
        if not os.path.exists(context.blend_data.filepath):
            self.report({'ERROR'}, 'Please save your Blend file before using '
                                   'the Blender Cloud addon.')
            self.quit()
            return

        if not await self.authenticate(context):
            return

        import pillarsdk.exceptions
        from .sdk import Manager
        from ..pillar import pillar_call
        from ..blender import preferences

        scene = context.scene

        # Save to a different file, specifically for Flamenco.
        context.window_manager.flamenco_status = 'SAVING'
        filepath = await self._save_blendfile(context)

        # Determine where the render output will be stored.
        render_output = render_output_path(context, filepath)
        if render_output is None:
            self.report({'ERROR'}, 'Current file is outside of project path.')
            self.quit()
            return
        self.log.info('Will output render files to %s', render_output)

        # BAT-pack the files to the destination directory.
        outdir, outfile, missing_sources = await self.bat_pack(filepath)
        if not outfile:
            return

        # Fetch Manager for doing path replacement.
        self.log.info('Going to fetch manager %s', self.user_id)
        prefs = preferences()

        manager_id = prefs.flamenco_manager.manager
        try:
            manager = await pillar_call(Manager.find, manager_id)
        except pillarsdk.exceptions.ResourceNotFound:
            self.report({'ERROR'}, 'Manager %s not found, refresh your managers in '
                                   'the Blender Cloud add-on settings.' % manager_id)
            self.quit()
            return

        # Create the job at Flamenco Server.
        context.window_manager.flamenco_status = 'COMMUNICATING'

        frame_range = scene.flamenco_render_frame_range.strip() or scene_frame_range(context)
        settings = {'blender_cmd': '{blender}',
                    'chunk_size': scene.flamenco_render_fchunk_size,
                    'filepath': manager.replace_path(outfile),
                    'frames': frame_range,
                    'render_output': manager.replace_path(render_output),
                    }

        # Add extra settings specific to the job type
        if scene.flamenco_render_job_type == 'blender-render-progressive':
            if scene.cycles.progressive == 'BRANCHED_PATH':
                samples = scene.cycles.aa_samples
            else:
                samples = scene.cycles.samples

            if scene.cycles.use_square_samples:
                samples **= 2

            settings['cycles_num_chunks'] = scene.flamenco_render_schunk_count
            settings['cycles_sample_count'] = samples
            settings['format'] = 'EXR'

        try:
            job_info = await create_job(self.user_id,
                                        prefs.project.project,
                                        manager_id,
                                        scene.flamenco_render_job_type,
                                        settings,
                                        'Render %s' % filepath.name,
                                        priority=scene.flamenco_render_job_priority,
                                        start_paused=scene.flamenco_start_paused)
        except Exception as ex:
            self.report({'ERROR'}, 'Error creating Flamenco job: %s' % ex)
            self.quit()
            return

        # Store the job ID in a file in the output dir.
        with open(str(outdir / 'jobinfo.json'), 'w', encoding='utf8') as outfile:
            import json

            job_info['missing_files'] = [str(mf) for mf in missing_sources]
            json.dump(job_info, outfile, sort_keys=True, indent=4)

        # We can now remove the local copy we made with bpy.ops.wm.save_as_mainfile().
        # Strictly speaking we can already remove it after the BAT-pack, but it may come in
        # handy in case of failures.
        try:
            self.log.info('Removing temporary file %s', filepath)
            filepath.unlink()
        except Exception as ex:
            self.report({'ERROR'}, 'Unable to remove file: %s' % ex)
            self.quit()
            return

        if prefs.flamenco_open_browser_after_submit:
            import webbrowser
            from urllib.parse import urljoin
            from ..blender import PILLAR_WEB_SERVER_URL

            url = urljoin(PILLAR_WEB_SERVER_URL, '/flamenco/jobs/%s/redir' % job_info['_id'])
            webbrowser.open_new_tab(url)

        # Do a final report.
        if missing_sources:
            names = (ms.name for ms in missing_sources)
            self.report({'WARNING'}, 'Flamenco job created with missing files: %s' %
                        '; '.join(names))
        else:
            self.report({'INFO'}, 'Flamenco job created.')

        self.quit()

    def quit(self):
        if bpy.context.window_manager.flamenco_status != 'ABORTED':
            bpy.context.window_manager.flamenco_status = 'DONE'
        super().quit()

    async def _save_blendfile(self, context):
        """Save to a different file, specifically for Flamenco.

        We shouldn't overwrite the artist's file.
        We can compress, since this file won't be managed by SVN and doesn't need diffability.
        """

        render = context.scene.render

        # Remember settings we need to restore after saving.
        old_use_file_extension = render.use_file_extension
        old_use_overwrite = render.use_overwrite
        old_use_placeholder = render.use_placeholder

        try:

            # The file extension should be determined by the render settings, not necessarily
            # by the setttings in the output panel.
            render.use_file_extension = True

            # Rescheduling should not overwrite existing frames.
            render.use_overwrite = False
            render.use_placeholder = False

            filepath = Path(context.blend_data.filepath).with_suffix('.flamenco.blend')
            self.log.info('Saving copy to temporary file %s', filepath)
            bpy.ops.wm.save_as_mainfile(filepath=str(filepath),
                                        compress=True,
                                        copy=True)
        finally:
            # Restore the settings we changed, even after an exception.
            render.use_file_extension = old_use_file_extension
            render.use_overwrite = old_use_overwrite
            render.use_placeholder = old_use_placeholder

        return filepath

    async def bat_pack(self, filepath: Path) -> (Path, typing.Optional[Path], typing.List[Path]):
        """BAT-packs the blendfile to the destination directory.

        Returns the path of the destination blend file.

        :param filepath: the blend file to pack (i.e. the current blend file)
        :returns: the destination directory, the destination blend file or None
            if there were errors BAT-packing, and a list of missing paths.
        """

        from datetime import datetime
        from ..blender import preferences

        prefs = preferences()

        # Create a unique directory that is still more or less identifyable.
        # This should work better than a random ID.
        unique_dir = '%s-%s-%s' % (datetime.now().isoformat('-').replace(':', ''),
                                   self.db_user['username'],
                                   filepath.stem)
        outdir = Path(prefs.flamenco_job_file_path) / unique_dir
        proj_abspath = bpy.path.abspath(prefs.cloud_project_local_path)
        projdir = Path(proj_abspath).resolve()
        exclusion_filter = (prefs.flamenco_exclude_filter or '').strip()

        self.log.debug('outdir : %s', outdir)
        self.log.debug('projdir: %s', projdir)

        try:
            outdir.mkdir(parents=True)
        except Exception as ex:
            self.log.exception('Unable to create output path %s', outdir)
            self.report({'ERROR'}, 'Unable to create output path: %s' % ex)
            self.quit()
            return outdir, None, []

        try:
            outfile, missing_sources = await bat_interface.copy(
                bpy.context, filepath, projdir, outdir, exclusion_filter)
        except bat_interface.FileTransferError as ex:
            self.log.error('Could not transfer %d files, starting with %s',
                           len(ex.files_remaining), ex.files_remaining[0])
            self.report({'ERROR'}, 'Unable to transfer %d files' % len(ex.files_remaining))
            self.quit()
            return outdir, None, []
        except bat_interface.Aborted:
            self.log.warning('BAT Pack was aborted')
            self.report({'WARNING'}, 'Aborted Flamenco file packing/transferring')
            self.quit()
            return outdir, None, []

        bpy.context.window_manager.flamenco_status = 'DONE'
        return outdir, outfile, missing_sources


def scene_frame_range(context) -> str:
    """Returns the frame range string for the current scene."""

    s = context.scene
    return '%i-%i' % (s.frame_start, s.frame_end)


class FLAMENCO_OT_scene_to_frame_range(FlamencoPollMixin, Operator):
    """Sets the scene frame range as the Flamenco render frame range."""
    bl_idname = 'flamenco.scene_to_frame_range'
    bl_label = 'Sets the scene frame range as the Flamenco render frame range'
    bl_description = __doc__.rstrip('.')

    def execute(self, context):
        context.scene.flamenco_render_frame_range = scene_frame_range(context)
        return {'FINISHED'}


class FLAMENCO_OT_copy_files(Operator,
                             FlamencoPollMixin,
                             async_loop.AsyncModalOperatorMixin):
    """Uses BAT to copy the current blendfile + dependencies to the target directory.

    This operator is not used directly, but can be useful for testing.
    """
    bl_idname = 'flamenco.copy_files'
    bl_label = 'Copy files to target'
    bl_description = __doc__.rstrip('.')

    stop_upon_exception = True

    async def async_execute(self, context) -> None:
        from pathlib import Path
        from ..blender import preferences

        prefs = preferences()
        exclusion_filter = (prefs.flamenco_exclude_filter or '').strip()

        storage_path = prefs.flamenco_job_file_path  # type: str

        try:
            outpath, missing_sources = await bat_interface.copy(
                context,
                Path(context.blend_data.filepath),
                Path(prefs.cloud_project_local_path),
                Path(storage_path),
                exclusion_filter
            )
        except bat_interface.FileTransferError as ex:
            self.log.error('Could not transfer %d files, starting with %s',
                           len(ex.files_remaining), ex.files_remaining[0])
            self.report({'ERROR'}, 'Unable to transfer %d files' % len(ex.files_remaining))
            self.quit()
            return
        except bat_interface.Aborted:
            self.log.warning('BAT Pack was aborted')
            self.report({'WARNING'}, 'Aborted Flamenco file packing/transferring')
            self.quit()
            return

        if missing_sources:
            names = (ms.name for ms in missing_sources)
            self.report({'ERROR'}, 'Missing source files: %s' % '; '.join(names))
        else:
            self.report({'INFO'}, 'Written %s' % outpath)
        context.window_manager.flamenco_status = 'DONE'
        self.quit()


class FLAMENCO_OT_abort(Operator, FlamencoPollMixin):
    """Aborts a running Flamenco file packing/transfer operation."""
    bl_idname = 'flamenco.abort'
    bl_label = 'Abort'
    bl_description = __doc__.rstrip('.')

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.window_manager.flamenco_status != 'ABORTING'

    def execute(self, context):
        context.window_manager.flamenco_status = 'ABORTING'
        bat_interface.abort()
        return {'FINISHED'}


class FLAMENCO_OT_explore_file_path(FlamencoPollMixin,
                                    Operator):
    """Opens the Flamenco job storage path in a file explorer.

    If the path cannot be found, this operator tries to open its parent.
    """

    bl_idname = 'flamenco.explore_file_path'
    bl_label = 'Open in file explorer'
    bl_description = __doc__.rstrip('.')

    path = StringProperty(name='Path', description='Path to explore', subtype='DIR_PATH')

    def execute(self, context):
        import platform
        import pathlib

        # Possibly open a parent of the path
        to_open = pathlib.Path(self.path)
        while to_open.parent != to_open:  # while we're not at the root
            if to_open.exists():
                break
            to_open = to_open.parent
        else:
            self.report({'ERROR'}, 'Unable to open %s or any of its parents.' % self.path)
            return {'CANCELLED'}
        to_open = str(to_open)

        if platform.system() == "Windows":
            import os
            os.startfile(to_open)

        elif platform.system() == "Darwin":
            import subprocess
            subprocess.Popen(["open", to_open])

        else:
            import subprocess
            subprocess.Popen(["xdg-open", to_open])

        return {'FINISHED'}


class FLAMENCO_OT_enable_output_path_override(Operator):
    """Enables the 'override output path' setting."""

    bl_idname = 'flamenco.enable_output_path_override'
    bl_label = 'Enable Overriding of Output Path'
    bl_description = 'Click to specify a non-default Output Path for this particular job'

    def execute(self, context):
        context.scene.flamenco_do_override_output_path = True
        return {'FINISHED'}


class FLAMENCO_OT_disable_output_path_override(Operator):
    """Disables the 'override output path' setting."""

    bl_idname = 'flamenco.disable_output_path_override'
    bl_label = 'disable Overriding of Output Path'
    bl_description = 'Click to use the default Output Path'

    def execute(self, context):
        context.scene.flamenco_do_override_output_path = False
        return {'FINISHED'}


async def create_job(user_id: str,
                     project_id: str,
                     manager_id: str,
                     job_type: str,
                     job_settings: dict,
                     job_name: str = None,
                     *,
                     priority: int = 50,
                     job_description: str = None,
                     start_paused=False) -> dict:
    """Creates a render job at Flamenco Server, returning the job object as dictionary."""

    import json
    from .sdk import Job
    from ..pillar import pillar_call

    job_attrs = {
        'status': 'queued',
        'priority': priority,
        'name': job_name,
        'settings': job_settings,
        'job_type': job_type,
        'user': user_id,
        'manager': manager_id,
        'project': project_id,
    }
    if job_description:
        job_attrs['description'] = job_description
    if start_paused:
        job_attrs['start_paused'] = True

    log.info('Going to create Flamenco job:\n%s',
             json.dumps(job_attrs, indent=4, sort_keys=True))

    job = Job(job_attrs)
    await pillar_call(job.create)

    log.info('Job created succesfully: %s', job._id)
    return job.to_dict()


def is_image_type(render_output_type: str) -> bool:
    """Determines whether the render output type is an image (True) or video (False)."""

    # This list is taken from rna_scene.c:273, rna_enum_image_type_items.
    video_types = {'AVI_JPEG', 'AVI_RAW', 'FRAMESERVER', 'FFMPEG', 'QUICKTIME'}
    return render_output_type not in video_types


@functools.lru_cache(1)
def _render_output_path(
        local_project_path: str,
        blend_filepath: Path,
        flamenco_job_output_strip_components: int,
        flamenco_job_output_path: str,
        render_image_format: str,
        flamenco_render_frame_range: str,
        include_rel_path: bool = True,
) -> typing.Optional[PurePath]:
    """Cached version of render_output_path()

    This ensures that redraws of the Flamenco Render and Add-on preferences panels
    is fast.
    """

    try:
        project_path = Path(bpy.path.abspath(local_project_path)).resolve()
    except FileNotFoundError:
        # Path.resolve() will raise a FileNotFoundError if the project path doesn't exist.
        return None

    try:
        blend_abspath = blend_filepath.resolve().absolute()
    except FileNotFoundError:
        # Path.resolve() will raise a FileNotFoundError if the path doesn't exist.
        return None

    try:
        proj_rel = blend_abspath.parent.relative_to(project_path)
    except ValueError:
        return None

    output_top = PurePath(flamenco_job_output_path)

    # Strip off '.flamenco' too; we use 'xxx.flamenco.blend' as job file, but
    # don't want to have all the output paths ending in '.flamenco'.
    stem = blend_filepath.stem
    if stem.endswith('.flamenco'):
        stem = stem[:-9]

    if include_rel_path:
        rel_parts = proj_rel.parts[flamenco_job_output_strip_components:]
        dir_components = output_top.joinpath(*rel_parts) / stem
    else:
        dir_components = output_top

    # Blender will have to append the file extensions by itself.
    if is_image_type(render_image_format):
        return dir_components / '######'
    return dir_components / flamenco_render_frame_range


def render_output_path(context, filepath: Path = None) -> typing.Optional[PurePath]:
    """Returns the render output path to be sent to Flamenco.

    :param context: the Blender context (used to find Flamenco preferences etc.)
    :param filepath: the Path of the blend file to render, or None for the current file.

    Returns None when the current blend file is outside the project path.
    """

    from ..blender import preferences

    scene = context.scene
    prefs = preferences()

    if filepath is None:
        filepath = Path(context.blend_data.filepath)

    if scene.flamenco_do_override_output_path:
        job_output_path = scene.flamenco_override_output_path
    else:
        job_output_path = prefs.flamenco_job_output_path

    return _render_output_path(
        prefs.cloud_project_local_path,
        filepath,
        prefs.flamenco_job_output_strip_components,
        job_output_path,
        scene.render.image_settings.file_format,
        scene.flamenco_render_frame_range,
        include_rel_path=not scene.flamenco_do_override_output_path,
    )


class FLAMENCO_PT_render(bpy.types.Panel, FlamencoPollMixin):
    bl_label = "Flamenco Render"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        from ..blender import preferences

        prefs = preferences()

        labeled_row = layout.split(0.25, align=True)
        labeled_row.label('Job Type:')
        labeled_row.prop(context.scene, 'flamenco_render_job_type', text='')

        labeled_row = layout.split(0.25, align=True)
        labeled_row.label('Frame Range:')
        prop_btn_row = labeled_row.row(align=True)
        prop_btn_row.prop(context.scene, 'flamenco_render_frame_range', text='')
        prop_btn_row.operator('flamenco.scene_to_frame_range', text='', icon='ARROW_LEFTRIGHT')

        layout.prop(context.scene, 'flamenco_render_job_priority')
        layout.prop(context.scene, 'flamenco_render_fchunk_size')
        layout.prop(context.scene, 'flamenco_start_paused')

        if getattr(context.scene, 'flamenco_render_job_type', None) == 'blender-render-progressive':
            layout.prop(context.scene, 'flamenco_render_schunk_count')

        paths_layout = layout.column(align=True)

        labeled_row = paths_layout.split(0.25, align=True)
        labeled_row.label('Storage:')
        prop_btn_row = labeled_row.row(align=True)
        prop_btn_row.label(prefs.flamenco_job_file_path)
        props = prop_btn_row.operator(FLAMENCO_OT_explore_file_path.bl_idname,
                                      text='', icon='DISK_DRIVE')
        props.path = prefs.flamenco_job_file_path

        render_output = render_output_path(context)
        if render_output is None:
            paths_layout.label('Unable to render with Flamenco, outside of project directory.')
            return

        labeled_row = paths_layout.split(0.25, align=True)
        labeled_row.label('Output:')
        prop_btn_row = labeled_row.row(align=True)

        if context.scene.flamenco_do_override_output_path:
            prop_btn_row.prop(context.scene, 'flamenco_override_output_path', text='')
            op = FLAMENCO_OT_disable_output_path_override.bl_idname
            icon = 'X'
        else:
            prop_btn_row.label(str(render_output))
            op = FLAMENCO_OT_enable_output_path_override.bl_idname
            icon = 'GREASEPENCIL'
        prop_btn_row.operator(op, icon=icon, text='')

        props = prop_btn_row.operator(FLAMENCO_OT_explore_file_path.bl_idname,
                                      text='', icon='DISK_DRIVE')
        props.path = str(render_output.parent)

        if context.scene.flamenco_do_override_output_path:
            labeled_row = paths_layout.split(0.25, align=True)
            labeled_row.label('Effective Output Path:')
            labeled_row.label(str(render_output))

        # Show current status of Flamenco.
        flamenco_status = context.window_manager.flamenco_status
        if flamenco_status in {'IDLE', 'ABORTED', 'DONE'}:
            layout.operator(FLAMENCO_OT_render.bl_idname,
                            text='Render on Flamenco',
                            icon='RENDER_ANIMATION')
            if bpy.app.debug:
                layout.operator(FLAMENCO_OT_copy_files.bl_idname)
        elif flamenco_status == 'INVESTIGATING':
            row = layout.row(align=True)
            row.label('Investigating your files')
            row.operator(FLAMENCO_OT_abort.bl_idname, text='', icon='CANCEL')
        elif flamenco_status == 'COMMUNICATING':
            layout.label('Communicating with Flamenco Server')
        elif flamenco_status == 'ABORTING':
            row = layout.row(align=True)
            row.label('Aborting, please wait.')
            row.operator(FLAMENCO_OT_abort.bl_idname, text='', icon='CANCEL')
        if flamenco_status == 'TRANSFERRING':
            row = layout.row(align=True)
            row.prop(context.window_manager, 'flamenco_progress',
                     text=context.window_manager.flamenco_status_txt)
            row.operator(FLAMENCO_OT_abort.bl_idname, text='', icon='CANCEL')
        elif flamenco_status != 'IDLE' and context.window_manager.flamenco_status_txt:
            layout.label(context.window_manager.flamenco_status_txt)


def activate():
    """Activates draw callbacks, menu items etc. for Flamenco."""

    global flamenco_is_active
    log.info('Activating Flamenco')
    flamenco_is_active = True
    _render_output_path.cache_clear()


def deactivate():
    """Deactivates draw callbacks, menu items etc. for Flamenco."""

    global flamenco_is_active
    log.info('Deactivating Flamenco')
    flamenco_is_active = False
    _render_output_path.cache_clear()


def flamenco_do_override_output_path_updated(scene, context):
    """Set the override paths to the default, if not yet set."""

    # Only set a default when enabling the override.
    if not scene.flamenco_do_override_output_path:
        return

    # Don't overwrite existing setting.
    if scene.flamenco_override_output_path:
        return

    from ..blender import preferences
    scene.flamenco_override_output_path = preferences().flamenco_job_output_path
    log.info('Setting Override Output Path to %s', scene.flamenco_override_output_path)


def register():
    from ..utils import redraw

    bpy.utils.register_class(FlamencoManagerGroup)
    bpy.utils.register_class(FLAMENCO_OT_fmanagers)
    bpy.utils.register_class(FLAMENCO_OT_render)
    bpy.utils.register_class(FLAMENCO_OT_scene_to_frame_range)
    bpy.utils.register_class(FLAMENCO_OT_copy_files)
    bpy.utils.register_class(FLAMENCO_OT_explore_file_path)
    bpy.utils.register_class(FLAMENCO_OT_enable_output_path_override)
    bpy.utils.register_class(FLAMENCO_OT_disable_output_path_override)
    bpy.utils.register_class(FLAMENCO_OT_abort)
    bpy.utils.register_class(FLAMENCO_PT_render)

    scene = bpy.types.Scene
    scene.flamenco_render_fchunk_size = IntProperty(
        name='Frame Chunk Size',
        description='Maximum number of frames to render per task',
        min=1,
        default=1,
    )
    scene.flamenco_render_schunk_count = IntProperty(
        name='Number of Sample Chunks',
        description='Number of Cycles samples chunks to use per frame',
        min=2,
        default=3,
        soft_max=10,
    )
    scene.flamenco_render_frame_range = StringProperty(
        name='Frame Range',
        description='Frames to render, in "printer range" notation'
    )
    scene.flamenco_render_job_type = EnumProperty(
        name='Job Type',
        items=[
            ('blender-render', 'Simple Render', 'Simple frame-by-frame render'),
            ('blender-render-progressive', 'Progressive Render',
             'Each frame is rendered multiple times with different Cycles sample chunks, then combined'),
        ]
    )

    scene.flamenco_start_paused = BoolProperty(
        name='Start Paused',
        description="When enabled, the job will be created in 'paused' state, rather than"
                    " 'queued'. The job will need manual queueing before it will start",
        default=False,
    )

    scene.flamenco_render_job_priority = IntProperty(
        name='Job Priority',
        min=0,
        default=50,
        max=100,
        description='Higher numbers mean higher priority'
    )

    scene.flamenco_do_override_output_path = BoolProperty(
        name='Override Output Path for this Job',
        description='When enabled, allows you to specify a non-default Output path '
                    'for this particular job',
        default=False,
        update=flamenco_do_override_output_path_updated
    )
    scene.flamenco_override_output_path = StringProperty(
        name='Override Output Path',
        description='Path where to store output files, should be accessible for Workers',
        subtype='DIR_PATH',
        default='')

    bpy.types.WindowManager.flamenco_status = EnumProperty(
        items=[
            ('IDLE', 'IDLE', 'Not doing anything.'),
            ('SAVING', 'SAVING', 'Saving your file.'),
            ('INVESTIGATING', 'INVESTIGATING', 'Finding all dependencies.'),
            ('TRANSFERRING', 'TRANSFERRING', 'Transferring all dependencies.'),
            ('COMMUNICATING', 'COMMUNICATING', 'Communicating with Flamenco Server.'),
            ('DONE', 'DONE', 'Not doing anything, but doing something earlier.'),
            ('ABORTING', 'ABORTING', 'User requested we stop doing something.'),
            ('ABORTED', 'ABORTED', 'We stopped doing something.'),
        ],
        name='flamenco_status',
        default='IDLE',
        description='Current status of the Flamenco add-on',
        update=redraw)

    bpy.types.WindowManager.flamenco_status_txt = StringProperty(
        name='Flamenco Status',
        default='',
        description='Textual description of what Flamenco is doing',
        update=redraw)

    bpy.types.WindowManager.flamenco_progress = IntProperty(
        name='Flamenco Progress',
        default=0,
        description='File transfer progress',
        subtype='PERCENTAGE',
        min=0,
        max=100,
        update=redraw)


def unregister():
    deactivate()
    bpy.utils.unregister_module(__name__)

    for name in ('flamenco_render_fchunk_size',
                 'flamenco_render_schunk_count',
                 'flamenco_render_frame_range',
                 'flamenco_render_job_type',
                 'flamenco_start_paused',
                 'flamenco_render_job_priority',
                 'flamenco_do_override_output_path',
                 'flamenco_override_output_path'):
        try:
            delattr(bpy.types.Scene, name)
        except AttributeError:
            pass
    try:
        del bpy.types.WindowManager.flamenco_status
    except AttributeError:
        pass
