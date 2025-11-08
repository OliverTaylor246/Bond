-- Function to increment stream counts when a stream is created
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
