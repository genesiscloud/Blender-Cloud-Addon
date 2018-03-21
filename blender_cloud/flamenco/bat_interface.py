"""BAT🦇 packing interface for Flamenco."""

import asyncio
import logging
import threading
import typing
import pathlib

import bpy
from blender_asset_tracer import pack
from blender_asset_tracer.pack import progress, transfer


log = logging.getLogger(__name__)

_running_packer = None  # type: pack.Packer
_packer_lock = threading.RLock()

# For using in other parts of the add-on, so only this file imports BAT.
Aborted = pack.Aborted
FileTransferError = transfer.FileTransferError


class BatProgress(progress.Callback):
    """Report progress of BAT Packing to the UI.

    Uses asyncio.run_coroutine_threadsafe() to ensure the UI is only updated
    from the main thread. This is required since we run the BAT Pack in a
    background thread.
    """

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.get_event_loop()

    def _set_attr(self, attr: str, value):

        async def do_it():
            setattr(bpy.context.window_manager, attr, value)

        asyncio.run_coroutine_threadsafe(do_it(), loop=self.loop)

    def _txt(self, msg: str):
        """Set a text in a thread-safe way."""
        self._set_attr('flamenco_status_txt', msg)

    def _status(self, status: str):
        """Set the flamenco_status property in a thread-safe way."""
        self._set_attr('flamenco_status', status)

    def _progress(self, progress: int):
        """Set the flamenco_progress property in a thread-safe way."""
        self._set_attr('flamenco_progress', progress)

    def pack_start(self) -> None:
        self._txt('Starting BAT Pack operation')

    def pack_done(self,
                  output_blendfile: pathlib.Path,
                  missing_files: typing.Set[pathlib.Path]) -> None:
        if missing_files:
            self._txt('There were %d missing files' % len(missing_files))
        else:
            self._txt('Pack of %s done' % output_blendfile.name)

    def pack_aborted(self):
        self._txt('Aborted')
        self._status('ABORTED')

    def trace_blendfile(self, filename: pathlib.Path) -> None:
        """Called for every blendfile opened when tracing dependencies."""
        self._txt('Inspecting %s' % filename.name)

    def trace_asset(self, filename: pathlib.Path) -> None:
        if filename.stem == '.blend':
            return
        self._txt('Found asset %s' % filename.name)

    def rewrite_blendfile(self, orig_filename: pathlib.Path) -> None:
        self._txt('Rewriting %s' % orig_filename.name)

    def transfer_file(self, src: pathlib.Path, dst: pathlib.Path) -> None:
        self._txt('Transferring %s' % src.name)

    def transfer_file_skipped(self, src: pathlib.Path, dst: pathlib.Path) -> None:
        self._txt('Skipped %s' % src.name)

    def transfer_progress(self, total_bytes: int, transferred_bytes: int) -> None:
        self._progress(round(100 * transferred_bytes / total_bytes))

    def missing_file(self, filename: pathlib.Path) -> None:
        # TODO(Sybren): report missing files in a nice way
        pass


async def copy(context,
               base_blendfile: pathlib.Path,
               project: pathlib.Path,
               target: pathlib.Path,
               exclusion_filter: str) -> typing.Tuple[pathlib.Path, typing.Set[pathlib.Path]]:
    """Use BAT🦇 to copy the given file and dependencies to the target location.

    :raises: FileTransferError if a file couldn't be transferred.
    :returns: the path of the packed blend file, and a set of missing sources.
    """
    global _running_packer

    loop = asyncio.get_event_loop()

    wm = bpy.context.window_manager

    with pack.Packer(base_blendfile, project, target) as packer:
        with _packer_lock:
            if exclusion_filter:
                packer.exclude(*exclusion_filter.split())

            packer.progress_cb = BatProgress()
            _running_packer = packer

        log.debug('awaiting strategise')
        wm.flamenco_status = 'INVESTIGATING'
        await loop.run_in_executor(None, packer.strategise)

        log.debug('awaiting execute')
        wm.flamenco_status = 'TRANSFERRING'
        await loop.run_in_executor(None, packer.execute)

        log.debug('done')
        wm.flamenco_status = 'DONE'

    with _packer_lock:
        _running_packer = None

    return packer.output_path, packer.missing_files


def abort() -> None:
    """Abort a running copy() call.

    No-op when there is no running copy(). Can be called from any thread.
    """

    with _packer_lock:
        if _running_packer is None:
            log.debug('No running packer, ignoring call to bat_abort()')
            return
        log.info('Aborting running packer')
        _running_packer.abort()
