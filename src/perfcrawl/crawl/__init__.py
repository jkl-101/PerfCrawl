"""Phase 3 site-wide crawler subsystem.

Cohesive home for the new discovery + scope/robots/sitemap + measurement-pass
code (D-01..D-15). Everything downstream of discovery (`measure_url`,
`write_outputs`, `write_run`, `canonical_key`, `page_slug`, the
`PageResult`/`RunRecord` models) is reused unchanged from Phases 1/2.
"""
