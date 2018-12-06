# Blender Cloud changelog


## Version 1.9.5 (in development)

- Requires Blender-Asset-Tracer 0.7 or newer.
- Fix crashing Blender when running in background mode (e.g. without GUI).
- Flamenco: Include extra job parameters to allow for encoding a video at the end of a render
  job that produced an image sequence.
- Flamenco: Compress all blend files, and not just the one we save from Blender.
- Flamenco: Store more info in the `jobinfo.json` file. This is mostly useful for debugging issues
  on the render farm, as now things like the exclusion filter and Manager settings are logged too.
- Flamenco: Allow BAT-packing of only those assets that are referred to by relative path (e.g.
  a path starting with `//`). Assets with an absolute path are ignored, and assumed to be reachable
  at the same path by the Workers.


## Version 1.9.4 (2018-11-01)

- Fixed Python 3.6 and Blender 2.79b incompatibilities accidentally introduced in 1.9.3.


## Version 1.9.3 (2018-10-30)

- Fix drawing of Attract strips in the VSE on Blender 2.8.


## Version 1.9.2 (2018-09-17)

- No changes, just a different filename to force a refresh on our
  hosting platform.


## Version 1.9.1 (2018-09-17)

- Fix issue with Python 3.7, which is used by current daily builds of Blender.


## Version 1.9 (2018-09-05)

- Last version to support Blender versions before 2.80!
- Replace BAM with BAT🦇.
- Don't crash the texture browser when an invalid texture is seen.
- Support colour strips as Attract shots.
- Flamenco: allow jobs to be created in 'paused' state.
- Flamenco: only show Flamenco Managers that are linked to the currently selected project.


## Version 1.8 (2018-01-03)

- Distinguish between 'please subscribe' (to get a new subscription) and 'please renew' (to renew an
  existing subscription).
- When re-opening the Texture Browser it now opens in the same folder as where it was when closed.
- In the texture browser, draw the components of the texture (i.e. which map types are available),
  such as 'bump, normal, specular'.
- Use Interface Scale setting from user preferences to draw the Texture Browser text.
- Store project-specific settings in the preferences, such as filesystem paths, for each project,
  and restore those settings when the project is selected again. Does not touch settings that
  haven't been set for the newly selected project. These settings are only saved when a setting
  is updated, so to save your current settings need to update a single setting; this saves all
  settings for the project.
- Added button in the User Preferences to open a Cloud project in your webbrowser.


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
