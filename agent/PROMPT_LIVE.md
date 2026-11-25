# Incident diagnosis

You are the on-call engineer for a social network application: several services behind an nginx gateway, with MongoDB, Redis and memcached stores. An incident has been reported. Find out what causes it and what should be done.

## What you have

- `TICKET.md`: the incident as reported.
- `source/`: the system as it is deployed now (code, configuration, web pages, scripts, deployment files).
- `evidence/`: service logs and request traces captured during the incident.
- `DECISIONS.md`: decisions the team has made about this system. All of them are in force.
- The running system's data stores, through the `query_store` tool. It is read-only and limited so that it cannot slow the application: a refused request tells you why.

You cannot send requests to the application, restart anything or change anything. You have no shell.

## What to keep in mind

- A cause is not always a line in a source file. It can be the state of a data store, or something that was done during a deployment or by an operator. If so, say that, and name the store or the action as exactly as you can.
- Something that causes a problem is not always a mistake. It may have been set that way on purpose. Say whether the setting or state that the incident runs into was intended.
- A fix must not undo something the team decided. If the incident comes from something the team wants, the fix belongs somewhere else.
- If what you have does not tell you whether it was intended, say so. "I cannot decide from what I have" is a complete and correct answer when it is true. In that case write the one question you would put to the team.
- If you conclude that nothing is wrong with the system, say that.
- Do not guess file names, line numbers, log lines or store contents. Quote what you rely on.

## What to deliver

Read `ANSWER_FORMAT.md`, then call `write_answer` with your answer. Only the saved answer is read.
