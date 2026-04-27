# AGENTS.md

Guidance for coding agents working in this repository.

## Project overview
This project is meant to be a caldav compliant server targeted at homelab/selfhosted/family use.
It is still in an early POC stage and we are slowly building it up for the moment.
It will have two main parts that will share some core logic but should otherwise remain isolated.
1. A web UI where users sign up and manage their calendars.
2. A dav entrypoint on its own sub URL path `/dav` that acts as the dav entrypoint for dav compatible clients.

It is written in rust using the AXUM framework and SQLX with postgres for the database layer and Askama for the templates.

## Code architecture guidelines
When writing code in the app keep these guidelines in mind.
* All responses must be an Askama template, including XML responses for the dav API.
* Do not use AXUM middleware for anything. For Things like authentication we should build a well factored helper module that exports high level functions to authenticate and retrieve user info that should be called directly in handler code. Not via abastracted wrappers or middleware.
* For the arbitrary properties that the dav compliance requires for some endpoints and models we will store the data as a JSON blob in the database.
* Only extract a new function when it will actually be used in at least two places. Do not extract one-off helper functions that are only called from a single call site.

## RFC documentation
The relavent RFCs have been included in the `./RFC` directory of this repository. Make liberal use of looking at the RFCs to find the relavent specification for what you are working on for any given moment.
You should ALWAYS consult the relavent RFC section before implementing a feature to ensure we are implementing things correctly.

## CalDAV parity checklist
Use `CALDAV_RADICALE_PARITY_CHECKLIST.md` as the implementation backlog for DAV/CalDAV compliance work. Before implementing a DAV feature, check that file for the target Radicale behavior, relevant source references, and existing progress.

When completing a DAV/CalDAV feature, update the checklist in the same change. Only mark an item complete when the behavior is implemented and covered by appropriate tests. If you discover a missing Radicale parity item while working, add it to the checklist instead of leaving it implicit.

## Vendored test suite
We have a test suite that apple abandoned a long time ago in the `./caldavtester-lab/` directory. The README.md file in that directory contains information about how to run it and its overall architecture.
The test suite should be run against our server to determine compliance. I have set the test suite up with what I believe is all of the features I want this server to support.

We also have a nix flake shell for the 'litmus' test suite that tests general dav compliance. It should also be run to verify changes and similarly documented as to its progress in the README.

# Project goal
The project goal is to implement enough of the caldav specification that this app can be used for the majority of real world use cases that a family would have.
Then on top of that implement calendar sharing as a server specific feature so that users can share applications.
For the pure caldav specifications that we want to meet we essentially want feature parity with the 'Radicale' project which is another self hostable calendar app.
I have checked out the Radicale code at ~/code/Radicale so we can inspect their code to see what caldav features they implement.

## Calendar sharing
The main thing that sets this calendar server apart from others is native first class feature support for calendar sharing.
The sharing is meant to be done in the web UI. The idea is that a calendar owner can share a calendar with another user
and then that second user sees the shared calendar as a calendar available to them when they connect their calendar apps to this server.

# Verification
Any time you make a change you should test using 'just verify' and then 'just integration-suites' commands.
