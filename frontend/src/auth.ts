import { reactive } from 'vue'
import { api, getToken, setToken, type User } from './api'

const RANK: Record<User['role'], number> = { viewer: 0, operator: 1, admin: 2 }

const state = reactive<{ user: User | null; loading: boolean; ready: boolean }>({
  user: null,
  loading: false,
  ready: false,
})

let booting: Promise<void> | null = null

export const auth = {
  state,

  /** Restore the session from the stored token exactly once per app start. */
  bootstrap(): Promise<void> {
    if (state.ready) return Promise.resolve()
    if (!getToken()) {
      state.ready = true
      return Promise.resolve()
    }
    if (!booting) {
      state.loading = true
      booting = api<User>('/api/auth/me')
        .then((u) => {
          state.user = u
        })
        .catch(() => {
          setToken(null)
          state.user = null
        })
        .finally(() => {
          state.loading = false
          state.ready = true
        })
    }
    return booting
  },

  login(token: string, user: User) {
    setToken(token)
    state.user = user
    state.ready = true
  },

  logout() {
    setToken(null)
    state.user = null
    state.ready = true
  },

  can(role: User['role']): boolean {
    return !!state.user && RANK[state.user.role] >= RANK[role]
  },
}
