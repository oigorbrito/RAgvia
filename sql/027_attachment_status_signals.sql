CREATE OR REPLACE VIEW process_attachment_status_counts AS
SELECT
    process_id,
    version_id,
    count(*)::int AS total_count,
    (count(*) FILTER (WHERE status = 'pending'))::int AS pending_count,
    (count(*) FILTER (WHERE status = 'ready'))::int AS ready_count,
    (count(*) FILTER (WHERE status = 'unavailable'))::int AS unavailable_count,
    (count(*) FILTER (WHERE status = 'corrupt'))::int AS corrupt_count,
    (count(*) FILTER (WHERE status = 'unreadable'))::int AS unreadable_count
FROM process_attachments
GROUP BY process_id, version_id;
