# Judit out-of-order completion recovery

This change makes request completion durable independently of webhook delivery order.

## Problem

A `request_completed` callback can arrive before its lawsuit response. Previously the finalizer could run immediately, find no staged lawsuit and complete as `no_lawsuit_response`. A later lawsuit callback would then have no way to reuse the original finalizer idempotency key because that completed job still occupied it.

## Migration

`009_judit_completion_delivery.sql` creates `judit_request_completions`, keyed by Judit `request_id`. The table records only completion state and timestamp; it does not duplicate lawsuit payloads or callback bodies.

## Runtime behavior

- completion records the durable marker and enqueues the normal `judit-finalize:{request_id}` job;
- lawsuit staging checks for a pre-existing completion marker;
- when completion was already observed, the lawsuit enqueues a repair finalizer keyed by both request and source response;
- repeated delivery of the same lawsuit cannot duplicate the repair job because the repair idempotency key is stable;
- finalization and summary enqueue remain idempotent and monotonic under concurrent normal/repair finalizers.

No historical backfill is required. The new table is empty on deployment and only records completions received after the migration.