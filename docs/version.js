/* Single source of truth for the student app's version.
 *
 * Bumped automatically by tools/bump_version.py whenever anything in docs/
 * changes -- see the pre-commit hook in tools/hooks/. The same string is
 * written into sw.js, which is what forces phones off stale cached code.
 *
 * Don't edit by hand; run `python3 tools/bump_version.py` instead.
 */
const MATTEND_VERSION = "4.3.13";
if (typeof window !== "undefined") window.MATTEND_VERSION = MATTEND_VERSION;
