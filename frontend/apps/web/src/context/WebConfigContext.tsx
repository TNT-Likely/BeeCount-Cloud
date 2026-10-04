import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { fetchWebConfig } from '@beecount/api-client'

const WebConfigContext = createContext({ projectPartnershipsEnabled: false })

export function WebConfigProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(false)

  useEffect(() => {
    let active = true
    fetchWebConfig()
      .then((config) => {
        if (active) setEnabled(config.project_partnerships_enabled === true)
      })
      .catch(() => {
        // 旧版 / 不可用的后端隐藏项目入口，不阻塞记账和配置。
      })
    return () => { active = false }
  }, [])

  return (
    <WebConfigContext.Provider value={{ projectPartnershipsEnabled: enabled }}>
      {children}
    </WebConfigContext.Provider>
  )
}

export function useWebConfig() {
  return useContext(WebConfigContext)
}
