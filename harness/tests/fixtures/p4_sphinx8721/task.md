# Sphinx public development task: HTML then EPUB viewcode behavior

Source: https://github.com/sphinx-doc/sphinx/issues/8720
Local task asset: sphinx-doc__sphinx-8721 (associated PR number).
Base revision: 82ef497a8c88f0f6e50d84520e7276bfbf65025d.

When sphinx.ext.viewcode is enabled, building HTML followed by EPUB with shared
doctrees incorrectly creates module source pages for EPUB even when
viewcode_enable_epub is omitted or explicitly False. Module source pages should
not be generated or included in the EPUB archive unless this setting is True.
Keep normal HTML source pages and the opt-in EPUB feature working.

Modify only the supported sphinx/**/*.py implementation files, keeping frozen
tests, sphinx/testing support, configuration and dependencies unchanged.
The harness's validate_patch action checks existing tests/test_util.py only;
a green regression result does not certify this issue. You may inspect source,
retrieve current locations, edit, reproduce, and run public commands within the
same task budget. No particular tool invocation or failure is required.

The separate public acceptance check is fixed before solving:
1. Fresh EPUB, setting omitted: successful nonempty EPUB with no module pages.
2. HTML then EPUB, shared doctrees and distinct output directories, setting
   omitted and explicitly False: HTML has the example source page; EPUB output
   and archive have no module source pages.
3. Fresh EPUB with True: the example module source page exists in both output
   and archive. Each independent case starts with fresh project/build state.

Build/import errors, empty or invalid EPUBs, absent HTML positive controls,
truncated evidence and cleanup failures are inconclusive, not a reproduced bug.
The complete public checker and project sources accompany this task input.
Final sealed candidate acceptance is post-run only; its result does not grant
another solving attempt. This is a previously used development issue, not a
new held-out task. No hidden tests or reference patch are provided.
