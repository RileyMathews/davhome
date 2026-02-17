---
name: vikunja
description: Use the vikunja api to create/manage/view tasks for this project on Vikunja
license: MIT
compatibility: opencode
metadata:
  audience: agents
---

## What I do
- Use the Vikunja API to find tasks for this project
- Use the Vikunja API to create tasks for this project
- Use the Vikunja API to manage tasks for this project

## When to use me

Use this skill whenever a task involves creating a task to do later. Finding a good task to work on right now. Or other project management type things.

## Prerequisites
Ensure the following environment variables are available. If they are not, ask the user to set them before continuing.
- VIKUNJA_API_KEY : The api token to use for making API calls
- VIKUNJA_REFERENCE_URL : The API that returns the JSON schema for the API
- VIKUNJA_PROJECT_ID : The project ID for the current project

## API Usage
When instructed to work with the Vikunja API. First fetch the URL $VIKUNJA_REFERENCE_URL which should be the exact fully qualified URL for the JSON schema of the API.

From there use the schema to determine the best endpoint to call for your need. Use the ID contained in $VIKUNJA_PROJECT_ID to constrain requests to the current project.

Use the API token stored in VIKUNJA_API_TOKEN for authentication.
