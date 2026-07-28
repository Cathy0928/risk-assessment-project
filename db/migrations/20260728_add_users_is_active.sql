ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
