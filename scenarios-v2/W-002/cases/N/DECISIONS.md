# Team decisions

Decisions the team has made about this system. Each one is in force.

## D-001  Posts are immutable
A post never changes after it is written. (Product)

## D-002  Encryption between services
Calls between services are not encrypted in this environment. (Security)

## D-003  Restart policy
Containers restart automatically when they stop. (Ops)

## D-004  Memcached
The memcached instances are caches. They may be restarted at any time. (DBA)

## D-005  Rows in the url-shorten store
The url-shorten store keeps one row for every link in every post. Only the short code is unique. (DBA)

## D-006  Post order
A post id is unique but is not a time order. Feeds are ordered by timestamp. (Platform)

## D-007  Password storage
A password is stored as the SHA-256 hash of the password plus a per-user salt. (Security)

## D-008  Ports
The gateway serves the web pages and the API on port 8080. Media is served on port 8081. (Ops)

## D-009  Tracing UI
The Jaeger UI is served on port 16686. (Observability)

## D-010  Login session length
A login session lasts 24 hours, with or without activity. (Security)

## D-011  Log level
Services log at info level. (Observability)

## D-012  JWT signing secret
The JWT signing secret stays the same for this release. (Security)

## D-013  Timeline page size
One timeline request may ask for any number of posts. (Platform)

## D-014  How fast the gateway follows address changes
The gateway follows a new container address within 10 seconds. (Ops)

## D-015  Tracing
In this environment every request is traced (sampling 1.0). (Observability)

## D-016  Retention of posts
Posts are kept for ever. (DBA)

## D-017  Test data
The seeded data set is socfb-Reed98: 962 users. (QA)

## D-018  Usernames
A username may contain letters, digits, hyphen and underscore. (Product)

## D-019  Connection pools
Each service may keep up to 512 connections to each service or store it calls. (Platform)

## D-020  Role of the timeline Redis stores
home-timeline-redis holds the only copy of the home feeds. It must be moved together with its data. (DBA)

## D-021  Request limits on the gateway
The gateway applies no per-address request limits, because all browser traffic arrives from one address. (Security)

## D-022  Call timeout
A call between services may take up to 10 seconds. (Platform)

## D-023  Upload size
The media front end accepts uploads up to 100 MB. (Product)

## D-024  Benchmark routes
The wrk2-api routes exist for benchmarks only and need no login. (Platform)

## D-025  MongoDB version
MongoDB stays on version 4.4.6. (DBA)

## D-026  Queue worker
write-home-timeline-service and RabbitMQ are not deployed. (Platform)

## D-027  Who may change credentials
Credentials are changed through the application only, never directly in the database. (Security)
