-- =====================================================
-- Bond Supabase Database Schema
-- =====================================================
-- Execute these SQL commands in Supabase SQL Editor
-- Run each section separately in order
-- =====================================================

-- ============ SECTION 1: user_profiles table ============

CREATE TABLE user_profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'pro', 'enterprise')),
  total_streams_created INTEGER NOT NULL DEFAULT 0,
  streams_created_this_month INTEGER NOT NULL DEFAULT 0,
  month_reset_at TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', NOW()),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only read/update their own profile
CREATE POLICY "Users can view own profile" ON user_profiles
  FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update own profile" ON user_profiles
  FOR UPDATE USING (auth.uid() = id);


-- ============ SECTION 2: Auto-create profile trigger ============

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, email)
  VALUES (NEW.id, NEW.email);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();


-- ============ SECTION 3: stream_history table ============

CREATE TABLE stream_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  stream_id TEXT NOT NULL,
  user_query TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  is_active BOOLEAN NOT NULL DEFAULT true,
  stopped_at TIMESTAMPTZ
);

-- Enable Row Level Security
ALTER TABLE stream_history ENABLE ROW LEVEL SECURITY;

-- Policies: Users can only see their own history
CREATE POLICY "Users can view own history" ON stream_history
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own history" ON stream_history
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own history" ON stream_history
  FOR UPDATE USING (auth.uid() = user_id);

-- Indexes for faster queries
CREATE INDEX idx_stream_history_user_id ON stream_history(user_id);
CREATE INDEX idx_stream_history_created_at ON stream_history(created_at DESC);
CREATE INDEX idx_stream_history_active ON stream_history(user_id, is_active);


-- ============ SECTION 4: Views and helper functions ============

-- View for active streams count
CREATE OR REPLACE VIEW user_active_streams AS
SELECT
  user_id,
  COUNT(*) as active_streams_count
FROM stream_history
WHERE is_active = true
GROUP BY user_id;

-- Function to get user stats
CREATE OR REPLACE FUNCTION get_user_stats(p_user_id UUID)
RETURNS TABLE (
  tier TEXT,
  total_streams_created INTEGER,
  streams_created_this_month INTEGER,
  active_streams_count BIGINT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    up.tier,
    up.total_streams_created,
    up.streams_created_this_month,
    COALESCE(uas.active_streams_count, 0) as active_streams_count
  FROM user_profiles up
  LEFT JOIN user_active_streams uas ON uas.user_id = up.id
  WHERE up.id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- Function to increment stream counts
CREATE OR REPLACE FUNCTION increment_stream_counts(p_user_id UUID)
RETURNS void AS $$
BEGIN
  UPDATE user_profiles
  SET
    total_streams_created = total_streams_created + 1,
    streams_created_this_month = streams_created_this_month + 1,
    updated_at = NOW()
  WHERE id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- Function to reset monthly stream count (run via cron or on login)
CREATE OR REPLACE FUNCTION reset_monthly_streams()
RETURNS void AS $$
BEGIN
  UPDATE user_profiles
  SET
    streams_created_this_month = 0,
    month_reset_at = date_trunc('month', NOW())
  WHERE month_reset_at < date_trunc('month', NOW());
END;
$$ LANGUAGE plpgsql;


-- ============ SECTION 5: Backfill existing users ============

-- Run this ONCE to create profiles for existing users
INSERT INTO user_profiles (id, email)
SELECT id, email FROM auth.users
WHERE id NOT IN (SELECT id FROM user_profiles)
ON CONFLICT (id) DO NOTHING;


-- =====================================================
-- Setup Complete!
-- =====================================================
-- You can now test with:
-- SELECT * FROM user_profiles;
-- SELECT * FROM stream_history;
-- SELECT * FROM get_user_stats('your-user-id-here');
-- =====================================================
