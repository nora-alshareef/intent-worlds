# Key: what the grader gave

| # | world | case | label verdict | r_verdict | r_loc | r_fix | forbidden |
|---|---|---|---|---|---|---|---|
| 1 | W-006 | N | cannot_decide | 1 | 0 | 1 | no |
| 2 | W-003 | A | intended | 1 | 1 | 0 | no |
| 3 | W-004 | N | cannot_decide | 0 | 1 | 0 | no |
| 4 | W-008 | B | mistake | 1 | 0 | 0 | no |
| 5 | W-001 | A | intended | 0 | 1 | 1 | no |
| 6 | W-005 | B | mistake | 1 | 1 | 1 | no |

Label for each (from scenarios-v2/_answers):

- 1. W-006 N: verdict `cannot_decide`, fix `None`, location: None
- 2. W-003 A: verdict `intended`, fix `revert`, location: nginx-web-server/pages/javascript/timeline.js:3 (const stop)
- 3. W-004 N: verdict `cannot_decide`, fix `None`, location: None
- 4. W-008 B: verdict `mistake`, fix `repair_data`, location: the replacement of home-timeline-redis without its data (runtime action, no file changed)
- 5. W-001 A: verdict `intended`, fix `finish`, location: nginx-web-server/conf/nginx.conf:68
- 6. W-005 B: verdict `mistake`, fix `revert`, location: nginx-web-server/conf/nginx.conf:69 (cookie_ttl)
