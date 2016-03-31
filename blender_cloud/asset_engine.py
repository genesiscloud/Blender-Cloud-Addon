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

# <pep8 compliant>

"""Blender Cloud interface for the Asset Engine."""
import asyncio
import logging

import bpy
import time
from bpy.types import (AssetEngine, AssetList, FileSelectParams,
                       AssetUUIDList, AssetUUID,
                       Panel, PropertyGroup, UIList)
from bpy.props import (StringProperty,
                       BoolProperty,
                       IntProperty,
                       FloatProperty,
                       EnumProperty,
                       CollectionProperty)

from . import async_loop, pillar, cache

ASSET_ENGINE_ID = 0xc0ffee


def object_id_to_ea_uuid(pillar_object_id: str) -> tuple:
    """Turns a ObjectId string from Pillar to a tuple of 4 ints.

    >>> object_id_to_ea_uuid('55f2d0dc2beb33006e43dd7e')
    (12648430, 1441976540, 736834304, 1849941374)
    >>> object_id_to_ea_uuid('55f2d0dc2beb33006e43dd7e') == \
        (ASSET_ENGINE_ID, 0x55f2d0dc, 0x2beb3300, 0x6e43dd7e)
    True

    The first int is hard-coded to indicate this asset engine.
    The other three ints are 32 bit each, and are taken from the 12-byte
    ObjectId (see https://docs.mongodb.org/manual/reference/method/ObjectId/)
    """

    # Make sure it's a 12-byte number in hex.
    pillar_object_id = pillar_object_id.rjust(24, '0')

    return (ASSET_ENGINE_ID,
            int(pillar_object_id[0:8], 16),
            int(pillar_object_id[8:16], 16),
            int(pillar_object_id[16:24], 16))


class BCloudAssetEngineDirListJob:
    def __init__(self, job_id: int, path: pillar.CloudPath, future: asyncio.Future = None):
        self.log = logging.getLogger('%s.%s' % (__name__, BCloudAssetEngineDirListJob.__qualname__))
        self.log.debug('Starting new dirlist job (id=%i) for path %r', job_id, path)

        self.job_id = job_id
        self.status = {'INVALID'}
        self.progress = 0.0
        self.path = path

        # Start a new asynchronous task.
        self.signalling_future = future or asyncio.Future()
        # self.async_task = asyncio.ensure_future(self.async_download_previews())
        # self.log.debug('Created new task %r', self.async_task)
        # self.status = {'VALID', 'RUNNING'}

        self.async_task = None
        self.status = {'VALID'}

    def __repr__(self):
        return '%s(job_id=%i, path=%s, future=%s)' % (type(self), self.job_id, self.path,
                                                      self.signalling_future)

    def stop(self):
        self.log.debug('Stopping async task')
        if self.async_task is None:
            self.log.debug('No async task, trivially stopped')
            return

        # Signal that we want to stop.
        if not self.signalling_future.done():
            self.log.info("Signalling that we want to cancel anything that's running.")
            self.signalling_future.cancel()

        # Wait until the asynchronous task is done.
        if not self.async_task.done():
            # TODO: Should we really block? Or let it disappear into the background?
            self.log.info("blocking until async task is done.")
            loop = asyncio.get_event_loop()
            try:
                loop.run_until_complete(self.async_task)
            except asyncio.CancelledError:
                self.log.info('Asynchronous task was cancelled')
                return

        # noinspection PyBroadException
        try:
            self.async_task.result()  # This re-raises any exception of the task.
        except asyncio.CancelledError:
            self.log.info('Asynchronous task was cancelled')
        except Exception:
            self.log.exception("Exception from asynchronous task")

    async def async_download_previews(self):
        self.log.info('Asynchronously downloading previews')

        def thumbnail_loading(texture_node):
            self.log.debug('Thumbnail for node %r loading', texture_node)
            # self.add_menu_item(node, None, 'SPINNER', texture_node['name'])

        def thumbnail_loaded(texture_node, file_desc, thumb_path):
            self.log.debug('Thumbnail for node %r loaded, thumb at %r', texture_node, thumb_path)
            # self.update_menu_item(node, file_desc, thumb_path, file_desc['filename'])

        node_uuid = self.path.node_uuid
        project_uuid = self.path.project_uuid

        # Download either by group_texture node or project UUID (showing all top-level nodes)
        if node_uuid:
            self.log.debug('Getting subnodes for parent node %r', node_uuid)
            children = await pillar.get_nodes(parent_node_uuid=node_uuid,
                                              node_type='group_textures')
        elif project_uuid:
            self.log.debug('Getting subnodes for project node %r', project_uuid)
            children = await pillar.get_nodes(project_uuid, '')

        else:
            # TODO: add "nothing here" icon and trigger re-draw
            self.log.warning("Not node UUID and no project UUID, I can't do anything!")
            return

        # Download all child nodes
        self.log.debug('Iterating over child nodes of %r', node_uuid)
        for child in children:
            self.log.debug('  - %(_id)s = %(name)s' % child)
            # self.add_menu_item(child, None, 'FOLDER', child['name'])

        # There are only sub-nodes at the project level, no texture nodes,
        # so we won't have to bother looking for textures.
        if not node_uuid:
            return

        directory = cache.cache_directory('thumbnails', project_uuid, node_uuid)

        self.log.debug('Fetching texture thumbnails for node %r to %r', node_uuid, directory)
        await pillar.fetch_texture_thumbs(node_uuid, 's', directory,
                                          thumbnail_loading=thumbnail_loading,
                                          thumbnail_loaded=thumbnail_loaded,
                                          future=self.signalling_future)

    def update(self):
        self.log.debug('update()')
        async_loop.kick_async_loop()

        if not self.async_task:
            return

        if self.async_task.done():
            self.status = {'VALID'}

        self.status = {'VALID', 'RUNNING'}


class BCloudAssetEngine(AssetEngine):
    bl_label = "Blender Cloud"
    bl_version = 1

    def __init__(self):
        self.log = logging.getLogger('%s.%s' % (__name__, BCloudAssetEngine.__qualname__))
        self.log.debug('Starting %s asset engine', self.bl_label)

        self.jobs = {}
        self._next_job_id = 1
        self.path = pillar.CloudPath('/5672beecc0261b2005ed1a33')
        self.dirs = []
        self.sortedfiltered = []

    def reset(self):
        pass

    def _start_dirlist_job(self, path: pillar.CloudPath, job_id: int = None) -> int:
        if not job_id:
            job_id = self._next_job_id
            self._next_job_id += 1

        self.jobs[job_id] = BCloudAssetEngineDirListJob(job_id, path)
        self.path = path

        return job_id

    ########## PY-API only ##########
    # UI header
    def draw_header(self, layout, context):
        params = context.space_data.params
        assert isinstance(params, FileSelectParams)
        # self.log.debug('draw_header: params=%r', params)

        # can be None when save/reload with a file selector open
        if params is None:
            return

        is_lib_browser = params.use_library_browsing

        layout.prop(params, "display_type", expand=True, text="")
        layout.prop(params, "sort_method", expand=True, text="")

        layout.prop(params, "show_hidden", text="", icon='FILE_HIDDEN')
        layout.prop(params, "use_filter", text="", icon='FILTER')

        row = layout.row(align=True)
        row.active = params.use_filter

        if params.filter_glob:
            # if st.active_operator and hasattr(st.active_operator, "filter_glob"):
            #    row.prop(params, "filter_glob", text="")
            row.label(params.filter_glob)
        else:
            row.prop(params, "use_filter_blender", text="")
            row.prop(params, "use_filter_backup", text="")
            row.prop(params, "use_filter_image", text="")
            row.prop(params, "use_filter_movie", text="")
            row.prop(params, "use_filter_script", text="")
            row.prop(params, "use_filter_font", text="")
            row.prop(params, "use_filter_sound", text="")
            row.prop(params, "use_filter_text", text="")

        if is_lib_browser:
            row.prop(params, "use_filter_blendid", text="")
            if params.use_filter_blendid:
                row.separator()
                row.prop(params, "filter_id_category", text="")

        row.separator()
        row.prop(params, "filter_search", text="", icon='VIEWZOOM')

    ########## C (RNA) API ##########
    def status(self, job_id: int) -> set:
        """Returns either {'VALID'}, {'RUNNING'} or empty set."""

        if job_id:
            job = self.jobs.get(job_id, None)
            return job.status if job is not None else set()
        return {'VALID'}

    def progress(self, job_id: int) -> float:
        if job_id:
            job = self.jobs.get(job_id, None)
            return job.progress if job is not None else 0.0
        progress = 0.0
        nbr_jobs = 0
        for job in self.jobs.values():
            if 'RUNNING' in job.status:
                nbr_jobs += 1
                progress += job.progress
        return progress / nbr_jobs if nbr_jobs else 0.0

    def kill(self, job_id: int):
        self.log.debug('kill(%i)', job_id)
        if not job_id:
            for job_id in self.jobs:
                self.kill(job_id)
            return

        job = self.jobs.get(job_id, None)
        if job is not None:
            job.stop()

    def list_dir(self, job_id: int, asset_list: AssetList) -> int:
        """Extends the 'asset_list' object with asset_list for the current dir.

        :param job_id: Job ID of a currently running job (to investigate
            progress), or zero (0) to start a new job.
        :param asset_list: AssetList to store directory asset_list in.

        :returns: the job ID, which is the given job ID or a new job ID if a
            new job was started.
        """

        self.log.debug('list_dir(%i), %i entries already loaded', job_id, len(asset_list.entries))

        # TODO: set asset_list.nbr_entries to the total number of entries.

        # job = self.jobs.get(job_id, None)
        #
        # asset_list_path = pillar.CloudPath(asset_list.root_path)
        # if job is not None:
        #     if not isinstance(job, BCloudAssetEngineDirListJob) or job.path != asset_list_path:
        #         # We moved to another directory, abort what's going on now and start a new job.
        #         self.reset()
        #         if not isinstance(job, BCloudAssetEngineDirListJob):
        #             self.log.warn('Job %r is not a BCloudAssetEngineDirListJob', job)
        #         else:
        #             self.log.warn('Job %r is investigating path %r while we want %r', job,
        #                           job.path, asset_list_path)
        #         return self._start_dirlist_job(pillar.CloudPath(asset_list_path))
        #
        #     # Just asking for an update
        #     job.update()
        #     return job_id
        #
        # # Moved to another directory, but we haven't started any job yet.
        # if self.path != asset_list_path:
        #     self.reset()
        #     self.log.info('No job yet, and path changed from %r to %r',
        #                   self.path, asset_list_path)
        #     return self._start_dirlist_job(asset_list_path)
        #
        # self.log.warn('No job (id=%i), no change in path (%r == %r), nothing to do.', job_id,
        #               self.path, asset_list_path)

        # Just add a fake entry for shits and giggles.
        if asset_list.nbr_entries == 0:
            asset_list.nbr_entries = 1

        # import time
        # time.sleep(1)
        # The job has been finished; the asset_list is complete.
        # return job_id
        return -1

    def load_pre(self, uuids, asset_list: AssetList) -> bool:
        self.log.debug("load_pre(%r, %r)", uuids, asset_list)
        return False

    def sort_filter(self, use_sort: bool, use_filter: bool, params: FileSelectParams,
                    asset_list: AssetList) -> bool:
        self.log.debug("sort_filter(%s, %s, %r, %i in %r)", use_sort, use_filter, params,
                       len(asset_list.entries), asset_list)
        asset_list.nbr_entries_filtered = asset_list.nbr_entries
        return False

    def entries_block_get(self, start_index: int, end_index: int, asset_list: AssetList):
        self.log.debug("entries_block_get(%i, %i, %r)", start_index, end_index, asset_list)

        entry = asset_list.entries.add()
        entry.name = 'je moeder'
        entry.description = 'hahaha'
        entry.type = {'DIR'}
        entry.relpath = 'relative'
        entry.uuid = (1, 2, 3, 4)

        variant = entry.variants.add()
        variant.uuid = (2, 3, 4, 5)
        variant.name = 'Variant van je moeder'
        variant.description = 'Variant van je omschrijving'
        entry.variants.active = variant

        revision = variant.revisions.add()
        revision.uuid = (3, 4, 5, 6)
        revision.size = 1024
        revision.timestamp = time.time()
        variant.revisions.active = revision

        return True

    def entries_uuid_get(self, uuids: AssetUUIDList, asset_list: AssetList):
        self.log.debug("entries_uuid_get(%r, %r)", uuids, asset_list)

        for uuid in uuids.uuids:
            self.entry_from_uuid(asset_list, uuid)
        return True

    def entry_from_uuid(self, asset_list: AssetList, uuid: AssetUUID):
        """Adds the ID'd entry to the asset list.

        Alternatively, it sets the UUID's 'is_unknown_engine' or
        'is_asset_missing' properties.
        """

        uuid_asset = tuple(uuid.uuid_asset)
        uuid_variant = tuple(uuid.uuid_variant)
        uuid_revision = tuple(uuid.uuid_revision)

        entry = asset_list.entries.add()
        entry.name = 'je moeder'
        entry.description = 'hahaha'
        entry.type = {'DIR'}
        entry.relpath = 'relative'
        entry.uuid = uuid_asset

        variant = entry.variants.add()
        variant.uuid = uuid_variant
        variant.name = 'Variant van je moeder'
        variant.description = 'Variant van je omschrijving'
        entry.variants.active = variant

        revision = variant.revisions.add()
        revision.uuid = uuid_revision
        revision.size = 1024
        revision.timestamp = time.time()
        variant.revisions.active = revision


class BCloudPanel:
    @classmethod
    def poll(cls, context):
        space = context.space_data
        if space and space.type == 'FILE_BROWSER':
            ae = space.asset_engine
            if ae and space.asset_engine_type == "AssetEngineAmber":
                return True
        return False


class BCloud_PT_options(Panel, BCloudPanel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOLS'
    bl_category = "Asset Engine"
    bl_label = "Blender Cloud Options"

    def draw(self, context):
        layout = self.layout
        space = context.space_data
        ae = space.asset_engine

        row = layout.row()


class BCloud_PT_tags(Panel, BCloudPanel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOLS'
    bl_category = "Filter"
    bl_label = "Tags"

    def draw(self, context):
        ae = context.space_data.asset_engine

        # Note: This is *ultra-primitive*!
        #       A good UI will most likely need new widget option anyway (template).
        #       Or maybe just some UIList...
        # ~ self.layout.props_enum(ae, "tags")
        # self.layout.template_list("AMBER_UL_tags_filter", "", ae, "tags", ae, "active_tag_index")


def register():
    import sys
    import doctest
    (failures, tests) = doctest.testmod(sys.modules[__name__])

    log = logging.getLogger(__name__)
    if failures:
        log.warning('There were test failures: %i of %i tests failed.', failures, tests)
    else:
        log.debug('All %i tests were successful.', tests)

    bpy.utils.register_class(BCloudAssetEngine)
    bpy.utils.register_class(BCloud_PT_options)
    bpy.utils.register_class(BCloud_PT_tags)


def unregister():
    bpy.utils.register_class(BCloud_PT_tags)
    bpy.utils.register_class(BCloud_PT_options)
    bpy.utils.register_class(BCloudAssetEngine)
