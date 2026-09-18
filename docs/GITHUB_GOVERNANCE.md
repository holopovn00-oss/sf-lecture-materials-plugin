# GitHub gates for SF Lecture Materials

This document is a repository policy record. It does not replace repository settings: a GitHub administrator must configure the protection described below.

## main

The `main` branch contains the current plugin version 0.1.0, the accepted Golden resources and their integrity records. Configure GitHub branch protection for `main` as follows:

- require a pull request before merge;
- require the `validate / validate` check to pass;
- block force pushes and branch deletion;
- do not treat a direct push or a green check after direct push as an accepted release.

The repository workflow cannot impose these repository settings by itself.

## Full PDF release gate

`Release validation / release-validate` is the complete mechanical gate for changes that affect PDF composition, math, Golden resources, their validators or their tests. It installs pinned Tectonic 0.15.0, primes an isolated task cache deliberately, runs the complete test suite with `SF_RELEASE_VALIDATION=1`, rejects every skipped test and validates pinned resources.

Run this workflow for the final candidate before creating or moving a release/tag. Its successful result still does not replace the visual review of every candidate page, semantic review or manual acceptance.
