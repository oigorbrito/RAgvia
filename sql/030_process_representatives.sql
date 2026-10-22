ALTER TABLE processes
ADD COLUMN IF NOT EXISTS representatives JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE processes
SET representatives = '[]'::jsonb
WHERE representatives IS NULL;
