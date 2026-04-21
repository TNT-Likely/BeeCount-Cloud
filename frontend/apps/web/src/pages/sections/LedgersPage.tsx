import { useNavigate } from 'react-router-dom'

import { LedgersSection } from '../../components/sections/LedgersSection'
import { useLedgers } from '../../context/LedgersContext'

/**
 * 账本列表页 —— 点击某个卡片把 activeLedgerId 切过去,然后跳到 /app/overview
 * 让用户直接进入该账本的纵览。LedgersSection 组件本身自治(读 useLedgers)。
 */
export function LedgersPage() {
  const navigate = useNavigate()
  const { setActiveLedgerId } = useLedgers()

  return (
    <LedgersSection
      onSelect={(ledgerId) => {
        setActiveLedgerId(ledgerId)
        navigate('/app/overview')
      }}
    />
  )
}
