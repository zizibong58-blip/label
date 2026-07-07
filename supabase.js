import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = "https://cpabezexbhcjfvxmgavz.supabase.co"
const SUPABASE_KEY = "sb_publishable_oB4lkE6rInHgW_wExgmyJQ_OrQe1ZxD"

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)
