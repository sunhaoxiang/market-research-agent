/**
 * 研究会话的外部 store（§12.4，P1-12）。
 *
 * 用 `useSyncExternalStore` 而不是 `useState` + `setState`：事件是高频的
 * （一次会话几十到几百条，工具调用密集时更多），每条都触发一次 React 状态更新
 * 会把渲染压垮。外部 store 让 React 按自己的节奏批量读快照。
 *
 * 不引入 Zustand / Redux：需要的就是「一个可订阅的不可变快照」，
 * 三十行足够，而多一个状态管理库就多一套心智负担。
 */

import { useCallback, useState, useSyncExternalStore } from "react";

import type { ResearchEvent } from "@mra/shared";

import { type ResearchViewState, initialState, reduce } from "@/lib/research/state";

export type ResearchStore = {
  getSnapshot: () => ResearchViewState;
  subscribe: (listener: () => void) => () => void;
  apply: (event: ResearchEvent) => void;
  /** 流层面的失败（连不上、HTTP 错误），与 session_failed 事件区分开 */
  failStream: (code: string, message: string) => void;
  reset: () => void;
};

export function createResearchStore(from: ResearchViewState = initialState): ResearchStore {
  let snapshot = from;
  const listeners = new Set<() => void>();

  const commit = (next: ResearchViewState): void => {
    // 引用不变就不通知：reduce 对未覆盖的事件类型会原样返回，
    // 心跳每 15 秒来一次，没必要为它重渲染整棵树
    if (next === snapshot) return;
    snapshot = next;
    for (const listener of listeners) listener();
  };

  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    apply: (event) => commit(reduce(snapshot, event)),
    failStream: (code, message) =>
      commit({
        ...snapshot,
        status: "failed",
        error: { code, message },
        completedAtMs: Date.now(),
      }),
    reset: () => commit(initialState),
  };
}

/** 订阅一个 store。store 本身由调用方持有，因此 hook 是无状态的。 */
export function useResearchState(store: ResearchStore): ResearchViewState {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

/**
 * 在组件里持有一个稳定的 store 实例。
 *
 * 用 `useState` 的惰性初始化而不是 `useRef` 的懒赋值：后者要在渲染期读写
 * `ref.current`，在 StrictMode 的双次渲染下行为不确定（`react-hooks/refs`
 * 也会报错）。`useState(fn)` 保证 `fn` 只被调用一次。
 */
export function useResearchStore(): ResearchStore {
  const [store] = useState(createResearchStore);
  return store;
}

/**
 * 每秒重取一次时间，用于 running 节点的实时耗时。
 *
 * 时钟刻意留在组件层而不进 reducer：reducer 必须是纯函数才能逐条喂事件来测，
 * 把 `Date.now()` 放进去会让每个断言都得先冻结时间。
 */
export function useTicker(active: boolean): number {
  const subscribe = useCallback(
    (listener: () => void) => {
      if (!active) return () => {};
      const timer = setInterval(listener, 1000);
      return () => clearInterval(timer);
    },
    [active],
  );

  return useSyncExternalStore(
    subscribe,
    () => Math.floor(Date.now() / 1000) * 1000,
    // 服务端渲染时没有"现在"可言，给 0 让耗时显示为 --
    () => 0,
  );
}
