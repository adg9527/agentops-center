import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken, type User } from './api'

interface AuthCtx {
  user: User | null
  loading: boolean
  login: (token: string, user: User) => void
  logout: () => void
  can: (role: User['role']) => boolean
}

const Ctx = createContext<AuthCtx>(null!)
const RANK = { viewer: 0, operator: 1, admin: 2 }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(!!getToken())

  useEffect(() => {
    if (!getToken()) return
    api<User>('/api/auth/me')
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  const value: AuthCtx = {
    user,
    loading,
    login: (token, u) => {
      setToken(token)
      setUser(u)
    },
    logout: () => {
      setToken(null)
      setUser(null)
    },
    can: (role) => !!user && RANK[user.role] >= RANK[role],
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
