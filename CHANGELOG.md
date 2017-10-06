# Blender Cloud changelog

## Version 1.7.5 (2017-10-06)

- Sorting the project list alphabetically.
- Renamed 'Job File Path' to 'Job Storage Path' so it's more explicit.
- Allow overriding the render output path on a per-scene basis.


## Version 1.7.4 (2017-09-05)

- Fix [T52621](https://developer.blender.org/T52621): Fixed class name collision upon add-on
  registration. This is checked since Blender 2.79.
- Fix [T48852](https://developer.blender.org/T48852): Screenshot no longer shows "Communicating with
  Blender Cloud".


## Version 1.7.3 (2017-08-08)

- Default to scene frame range when no frame range is given.
- Refuse to render on Flamenco before blend file is saved at least once.
- Fixed some Windows-specific issues.


## Version 1.7.2 (2017-06-22)

- Fixed compatibility with Blender 2.78c.


## Version 1.7.1 (2017-06-13)

- Fixed asyncio issues on Windows


## Version 1.7.0 (2017-06-09)

- Fixed reloading after upgrading from 1.4.4 (our last public release).
- Fixed bug handling a symlinked project path.
- Added support for Manager-defined path replacement variables.


## Version 1.6.4 (2017-04-21)

- Added file exclusion filter for Flamenco. A filter like `*.abc;*.mkv;*.mov` can be
  used to prevent certain files from being copied to the job storage directory.
  Requires a Blender that is bundled with BAM 1.1.7 or newer.


## Version 1.6.3 (2017-03-21)

- Fixed bug where local project path wasn't shown for projects only set up for Flamenco
  (and not Attract).
- Added this CHANGELOG.md file, which will contain user-relevant changes.


## Version 1.6.2 (2017-03-17)

- Flamenco: when opening non-existing file path, open parent instead
- Fix T50954: Improve Blender Cloud add-on project selector


## Version 1.6.1 (2017-03-07)

- Show error in GUI when Blender Cloud is unreachable
- Fixed sample count when using branched path tracing


## Version 1.6.0 (2017-02-14)

- Default to frame chunk size of 1 (instead of 10).
- Turn off "use overwrite" and "use placeholder" for Flamenco blend files.
- Fixed bugs when blendfile is outside the project directory


## Older versions

For the history of older versions, please refer to the
[Git history](https://developer.blender.org/diffusion/BCA/)
