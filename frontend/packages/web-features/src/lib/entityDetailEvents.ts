/**
 * 实体详情弹窗的跨包事件约定(0032)。
 *
 * web-features 是纯组件库,不能反向依赖 apps/web(路由 / 弹窗容器都在那),
 * 但流水行上的「定期存款」chip 又要能点开详情弹窗 —— 用 window CustomEvent
 * 解耦:这里只定义事件名和派发函数,apps/web 的 GlobalEntityDialogs 负责监听
 * 并渲染弹窗。事件名常量放这里作为唯一真理源,apps/web 引用而不是抄字符串。
 */

export const DETAIL_TIME_DEPOSIT_EVENT = 'bee:open-detail-td'

/** 按 sync_id 打开定期存款详情弹窗(数据由监听方现拉)。 */
export function dispatchOpenTimeDepositDetail(timeDepositId: string): void {
  window.dispatchEvent(
    new CustomEvent<string>(DETAIL_TIME_DEPOSIT_EVENT, { detail: timeDepositId }),
  )
}
