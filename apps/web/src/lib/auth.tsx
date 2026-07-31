import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

/**
 * Authentication layer.
 *
 * When VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are configured, Supabase
 * email OTP auth is used (with Row Level Security enforced server-side; see
 * supabase/migrations). Without configuration the app runs in a clearly
 * labeled LOCAL DEMO session so every workflow remains demonstrable offline.
 * No sportsbook credentials of any kind are ever stored.
 */

export interface AuthUser {
  id: string;
  email: string;
  isLocalDemo: boolean;
}

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  supabaseConfigured: boolean;
  signInWithEmail: (email: string) => Promise<{ error?: string; sentMagicLink?: boolean }>;
  signInLocalDemo: () => void;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

const supabase: SupabaseClient | null = url && anonKey ? createClient(url, anonKey) : null;

const LOCAL_KEY = 'fde-local-demo-session-v1';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (supabase) {
      supabase.auth.getSession().then(({ data }) => {
        const s = data.session;
        setUser(s ? { id: s.user.id, email: s.user.email ?? '', isLocalDemo: false } : null);
        setLoading(false);
      });
      const { data: sub } = supabase.auth.onAuthStateChange((_evt, s) => {
        setUser(s ? { id: s.user.id, email: s.user.email ?? '', isLocalDemo: false } : null);
      });
      return () => sub.subscription.unsubscribe();
    }
    const raw = localStorage.getItem(LOCAL_KEY);
    if (raw) {
      try {
        setUser(JSON.parse(raw) as AuthUser);
      } catch {
        localStorage.removeItem(LOCAL_KEY);
      }
    }
    setLoading(false);
    return undefined;
  }, []);

  const signInWithEmail = useCallback(async (email: string) => {
    if (!supabase) return { error: 'Supabase is not configured. Use the local demo session.' };
    const { error } = await supabase.auth.signInWithOtp({ email });
    return error ? { error: error.message } : { sentMagicLink: true };
  }, []);

  const signInLocalDemo = useCallback(() => {
    const demo: AuthUser = { id: 'demo-user', email: 'analyst@local.demo', isLocalDemo: true };
    localStorage.setItem(LOCAL_KEY, JSON.stringify(demo));
    setUser(demo);
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
    localStorage.removeItem(LOCAL_KEY);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, supabaseConfigured: !!supabase, signInWithEmail, signInLocalDemo, signOut }),
    [user, loading, signInWithEmail, signInLocalDemo, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
