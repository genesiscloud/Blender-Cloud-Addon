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
from pathlib import Path, PurePath
import typing

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene, PropertyGroup
from bpy.props import StringProperty, EnumProperty, PointerProperty, BoolProperty, IntProperty

from .. import async_loop, pillar
from ..utils import pyside_cache, redraw

log = logging.getLogger(__name__)


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
        description='Which Flamenco Manager to use for jobs')

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


class FLAMENCO_OT_fmanagers(async_loop.AsyncModalOperatorMixin,
                            pillar.AuthenticatedPillarOperatorMixin,
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
                         Operator):
    """Performs a Blender render on Flamenco."""
    bl_idname = 'flamenco.render'
    bl_label = 'Render on Flamenco'
    bl_description = __doc__.rstrip('.')

    stop_upon_exception = True
    log = logging.getLogger('%s.FLAMENCO_OT_render' % __name__)

    async def async_execute(self, context):
        if not await self.authenticate(context):
            return

        context.window_manager.progress_begin(0, 4)
        context.window_manager.progress_update(1)

        from pillarsdk import exceptions as sdk_exceptions
        from ..blender import preferences

        filepath = Path(context.blend_data.filepath)
        scene = context.scene

        # The file extension should be determined by the render settings, not necessarily
        # by the setttings in the output panel.
        scene.render.use_file_extension = True
        bpy.ops.wm.save_mainfile()

        # Determine where the render output will be stored.
        render_output = render_output_path(context)
        if render_output is None:
            self.report({'ERROR'}, 'Current file is outside of project path.')
            self.quit()
            return
        self.log.info('Will output render files to %s', render_output)

        # BAM-pack the files to the destination directory.
        outfile, missing_sources = await self.bam_pack(filepath)
        if not outfile:
            return

        context.window_manager.progress_update(3)

        # Create the job at Flamenco Server.
        prefs = preferences()

        settings = {'blender_cmd': '{blender}',
                    'chunk_size': scene.flamenco_render_chunk_size,
                    'filepath': str(outfile),
                    'frames': scene.flamenco_render_frame_range,
                    'render_output': str(render_output),
                    }
        try:
            job_info = await create_job(self.user_id,
                                        prefs.attract_project.project,
                                        prefs.flamenco_manager.manager,
                                        scene.flamenco_render_job_type,
                                        settings,
                                        'Render %s' % filepath.name,
                                        priority=scene.flamenco_render_job_priority)
        except sdk_exceptions.ResourceInvalid as ex:
            self.report({'ERROR'}, 'Error creating Flamenco job: %s' % ex)
            self.quit()
            return

        # Store the job ID in a file in the output dir.
        with open(str(outfile.parent / 'jobinfo.json'), 'w', encoding='utf8') as outfile:
            import json

            job_info['missing_files'] = [str(mf) for mf in missing_sources]
            json.dump(job_info, outfile, sort_keys=True, indent=4)

        # Do a final report.
        if missing_sources:
            names = (ms.name for ms in missing_sources)
            self.report({'WARNING'}, 'Flamenco job created with missing files: %s' %
                        '; '.join(names))
        else:
            self.report({'INFO'}, 'Flamenco job created.')
        self.quit()

    def quit(self):
        super().quit()
        bpy.context.window_manager.progress_end()

    async def bam_pack(self, filepath: Path) -> (typing.Optional[Path], typing.List[Path]):
        """BAM-packs the blendfile to the destination directory.

        Returns the path of the destination blend file.

        :param filepath: the blend file to pack (i.e. the current blend file)
        :returns: the destination blend file, or None if there were errors BAM-packing,
            and a list of missing paths.
        """

        from datetime import datetime
        from ..blender import preferences
        from . import bam_interface

        prefs = preferences()

        # Create a unique directory that is still more or less identifyable.
        # This should work better than a random ID.
        # BAM doesn't like output directories that end in '.blend'.
        unique_dir = '%s-%s-%s' % (datetime.now().isoformat('-').replace(':', ''),
                                   self.db_user['username'],
                                   filepath.stem)
        outdir = Path(prefs.flamenco_job_file_path) / unique_dir
        outfile = outdir / filepath.name

        try:
            outdir.mkdir(parents=True)
        except Exception as ex:
            self.log.exception('Unable to create output path %s', outdir)
            self.report({'ERROR'}, 'Unable to create output path: %s' % ex)
            self.quit()
            return None, []

        try:
            missing_sources = await bam_interface.bam_copy(filepath, outfile)
        except bam_interface.CommandExecutionError as ex:
            self.log.exception('Unable to execute BAM pack')
            self.report({'ERROR'}, 'Unable to execute BAM pack: %s' % ex)
            self.quit()
            return None, []

        return outfile, missing_sources


class FLAMENCO_OT_scene_to_frame_range(Operator):
    """Sets the scene frame range as the Flamenco render frame range."""
    bl_idname = 'flamenco.scene_to_frame_range'
    bl_label = 'Sets the scene frame range as the Flamenco render frame range'
    bl_description = __doc__.rstrip('.')

    def execute(self, context):
        s = context.scene
        s.flamenco_render_frame_range = '%i-%i' % (s.frame_start, s.frame_end)
        return {'FINISHED'}


class FLAMENCO_OT_copy_files(Operator,
                             async_loop.AsyncModalOperatorMixin):
    """Uses BAM to copy the current blendfile + dependencies to the target directory."""
    bl_idname = 'flamenco.copy_files'
    bl_label = 'Copy files to target'
    bl_description = __doc__.rstrip('.')

    stop_upon_exception = True

    async def async_execute(self, context):
        from pathlib import Path
        from . import bam_interface
        from ..blender import preferences

        missing_sources = await bam_interface.bam_copy(
            Path(context.blend_data.filepath),
            Path(preferences().flamenco_job_file_path),
        )

        if missing_sources:
            names = (ms.name for ms in missing_sources)
            self.report({'ERROR'}, 'Missing source files: %s' % '; '.join(names))

        self.quit()


class FLAMENCO_OT_explore_file_path(Operator):
    """Opens the Flamenco job storage path in a file explorer."""
    bl_idname = 'flamenco.explore_file_path'
    bl_label = 'Open in file explorer'
    bl_description = __doc__.rstrip('.')

    path = StringProperty(name='Path', description='Path to explore', subtype='DIR_PATH')

    def execute(self, context):
        import platform
        import subprocess
        import os

        if platform.system() == "Windows":
            os.startfile(self.path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.path])
        else:
            subprocess.Popen(["xdg-open", self.path])

        return {'FINISHED'}


async def create_job(user_id: str,
                     project_id: str,
                     manager_id: str,
                     job_type: str,
                     job_settings: dict,
                     job_name: str = None,
                     *,
                     priority: int = 50,
                     job_description: str = None) -> dict:
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
        blend_filepath: str,
        flamenco_job_output_strip_components: int,
        flamenco_job_output_path: str,
        render_image_format: str,
        flamenco_render_frame_range: str,
) -> typing.Optional[PurePath]:
    """Cached version of render_output_path()

    This ensures that redraws of the Flamenco Render and Add-on preferences panels
    is fast.
    """

    project_path = Path(bpy.path.abspath(local_project_path)).resolve()
    blendfile = Path(blend_filepath)

    try:
        proj_rel = blendfile.parent.relative_to(project_path)
    except ValueError:
        log.exception('Current file is outside of project path %s', project_path)
        return None

    rel_parts = proj_rel.parts[flamenco_job_output_strip_components:]
    output_top = Path(flamenco_job_output_path)
    dir_components = output_top.joinpath(*rel_parts) / blendfile.stem

    # Blender will have to append the file extensions by itself.
    if is_image_type(render_image_format):
        return dir_components / '#####'
    return dir_components / flamenco_render_frame_range


def render_output_path(context) -> typing.Optional[PurePath]:
    """Returns the render output path to be sent to Flamenco.

    Returns None when the current blend file is outside the project path.
    """

    from ..blender import preferences

    scene = context.scene
    prefs = preferences()

    return _render_output_path(
        prefs.attract_project_local_path,
        context.blend_data.filepath,
        prefs.flamenco_job_output_strip_components,
        prefs.flamenco_job_output_path,
        scene.render.image_settings.file_format,
        scene.flamenco_render_frame_range,
    )


class FLAMENCO_PT_render(bpy.types.Panel):
    bl_label = "Flamenco Render"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        from ..blender import preferences

        prefs = preferences()

        layout.prop(context.scene, 'flamenco_render_job_priority')
        layout.prop(context.scene, 'flamenco_render_chunk_size')

        labeled_row = layout.split(0.2, align=True)
        labeled_row.label('Job type:')
        labeled_row.prop(context.scene, 'flamenco_render_job_type', text='')

        labeled_row = layout.split(0.2, align=True)
        labeled_row.label('Frame range:')
        prop_btn_row = labeled_row.row(align=True)
        prop_btn_row.prop(context.scene, 'flamenco_render_frame_range', text='')
        prop_btn_row.operator('flamenco.scene_to_frame_range', text='', icon='ARROW_LEFTRIGHT')

        readonly_stuff = layout.column(align=True)
        labeled_row = readonly_stuff.split(0.2, align=True)
        labeled_row.label('Storage:')
        prop_btn_row = labeled_row.row(align=True)
        prop_btn_row.label(prefs.flamenco_job_file_path)
        props = prop_btn_row.operator(FLAMENCO_OT_explore_file_path.bl_idname,
                                      text='', icon='DISK_DRIVE')
        props.path = prefs.flamenco_job_file_path

        labeled_row = readonly_stuff.split(0.2, align=True)
        labeled_row.label('Output:')
        prop_btn_row = labeled_row.row(align=True)
        render_output = render_output_path(context)
        if render_output is None:
            prop_btn_row.label('Unable to render with Flamenco, outside of project directory.')
        else:
            prop_btn_row.label(str(render_output))
            props = prop_btn_row.operator(FLAMENCO_OT_explore_file_path.bl_idname,
                                          text='', icon='DISK_DRIVE')
            props.path = str(render_output.parent)

            layout.operator(FLAMENCO_OT_render.bl_idname,
                            text='Render on Flamenco',
                            icon='RENDER_ANIMATION')


def register():
    bpy.utils.register_class(FlamencoManagerGroup)
    bpy.utils.register_class(FLAMENCO_OT_fmanagers)
    bpy.utils.register_class(FLAMENCO_OT_render)
    bpy.utils.register_class(FLAMENCO_OT_scene_to_frame_range)
    bpy.utils.register_class(FLAMENCO_OT_copy_files)
    bpy.utils.register_class(FLAMENCO_OT_explore_file_path)
    bpy.utils.register_class(FLAMENCO_PT_render)

    scene = bpy.types.Scene
    scene.flamenco_render_chunk_size = IntProperty(
        name='Chunk size',
        description='Maximum number of frames to render per task',
        default=10,
    )
    scene.flamenco_render_frame_range = StringProperty(
        name='Frame range',
        description='Frames to render, in "printer range" notation'
    )
    scene.flamenco_render_job_type = EnumProperty(
        name='Job type',
        items=[
            ('blender-render', 'Simple Blender render', 'Not tiled, not resumable, just render'),
        ],
        description='Flamenco render job type',
    )
    scene.flamenco_render_job_priority = IntProperty(
        name='Job priority',
        min=0,
        default=50,
        max=100,
        description='Higher numbers mean higher priority'
    )


def unregister():
    bpy.utils.unregister_module(__name__)

    try:
        del bpy.types.Scene.flamenco_render_chunk_size
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.flamenco_render_frame_range
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.flamenco_render_job_type
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.flamenco_render_job_priority
    except AttributeError:
        pass
