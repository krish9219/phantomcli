# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Phantom v1.0.0.
# Builds a single `phantom` binary that bundles the legacy `omnicli`
# package (v3 surface) + the active `phantom` package (v1.0 features:
# daemon, swarm, importers, mcp, dashboard, plugins, selfdev, voice).
#
# Build:
#     pyinstaller --clean phantomcli.spec
# Output:
#     dist/phantom            (single ELF / Mach-O)
#
# Verify the binary:
#     dist/phantom version
#     dist/phantom bench --json
#

block_cipher = None

datas = [
    # legacy v3 (omnicli) — kept so `phantom chat` still works on this binary
    ('omnicli/templates', 'omnicli/templates'),
    ('omnicli/tui.py', 'omnicli'),
    ('omnicli/settings.py', 'omnicli'),
    ('omnicli/commands.py', 'omnicli'),
    # v1.0 new surfaces
    ('phantom/dashboard/static', 'phantom/dashboard/static'),
    ('phantom/plugins/builtin', 'phantom/plugins/builtin'),
    ('phantom/skills/builtin', 'phantom/skills/builtin'),
    ('version.json', '.'),
]

hiddenimports = [
    # third-party
    'stdiomask', 'fastapi', 'uvicorn',
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'sqlite3', 'cryptography', 'plotext', 'websockets', 'jinja2',
    'packaging', 'rich', 'typer', 'click',
    # phantom v1.0 sub-packages — eager-discoverable so the bundler picks them up
    'phantom.daemon', 'phantom.daemon.server', 'phantom.daemon.client', 'phantom.daemon.protocol',
    'phantom.swarm', 'phantom.swarm.runner',
    'phantom.selfdev', 'phantom.selfdev.runner',
    'phantom.memory.importers',
    'phantom.memory.importers.claude_code',
    'phantom.memory.importers.codex',
    'phantom.memory.importers.opencode',
    'phantom.memory.importers.orchestrator',
    'phantom.mcp.import_config',
    'phantom.config.providers',
    'phantom.voice.dictate',
    'phantom.cli.bench', 'phantom.cli.swarm_cmd', 'phantom.cli.selfdev_cmd',
    'phantom.cli.dictate_cmd', 'phantom.cli.memory_cmd',
    'phantom.cli.mcp_import_cmd', 'phantom.cli.provider_cmd',
    # built-in plugins shipped in v1.0
    'phantom.plugins.builtin.github_pr',
    'phantom.plugins.builtin.web_screenshot',
    'phantom.plugins.builtin.code_review',
    'phantom.plugins.builtin.clock',
    'phantom.plugins.builtin.weather',
    'phantom.plugins.builtin.todo',
    'phantom.plugins.builtin.code_search',
    'phantom.plugins.builtin.gh_search',
]

a = Analysis(
    ['bin_phantom.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'test',
        'pytest', '_pytest',
        'IPython', 'jupyter',
    ],
    noarchive=False,
    optimize=2,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='phantom',
    debug=False,
    bootloader_ignore_signals=False,
    # strip=False: GNU `strip` (the only one on Windows hosted runners,
    # via MinGW) corrupts the bundled python3xx.dll, producing a binary
    # that dies at launch with "Failed to load Python DLL". The size
    # win on POSIX is small enough that disabling everywhere keeps the
    # spec single-platform-clean.
    strip=False,
    # UPX compression makes the binary smaller but adds 100-300 ms to
    # every invocation while the bootloader decompresses into /tmp.
    # Phantom's whole performance pitch is sub-50 ms perceived start
    # via the daemon — we will not pay UPX's tax on the cold path.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
