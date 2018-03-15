"""BAT🦇 packing interface for Flamenco."""

import asyncio
import logging
import typing
from pathlib import Path

from blender_asset_tracer import pack
from blender_asset_tracer.pack.transfer import FileTransferError

log = logging.getLogger(__name__)


async def bat_copy(base_blendfile: Path,
                   project: Path,
                   target: Path,
                   exclusion_filter: str) -> typing.Tuple[Path, typing.Set[Path]]:
    """Use BAT🦇 to copy the given file and dependencies to the target location.

    :raises: FileTransferError if a file couldn't be transferred.
    :returns: the path of the packed blend file, and a set of missing sources.
    """

    loop = asyncio.get_event_loop()

    with pack.Packer(base_blendfile, project, target) as packer:
        if exclusion_filter:
            packer.exclude(*exclusion_filter.split())
        log.debug('awaiting strategise')
        await loop.run_in_executor(None, packer.strategise)
        log.debug('awaiting execute')
        await loop.run_in_executor(None, packer.execute)
        log.debug('done')

    return packer.output_path, packer.missing_files
