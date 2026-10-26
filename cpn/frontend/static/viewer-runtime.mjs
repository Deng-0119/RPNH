import { messageError } from './i18n.mjs';
/** Bounded browser transport and layout work. These controls never execute a task. */
export function viewerError(code, topic = 'connection', status) {
  const summaries={viewer_read_timeout:'读取超时；检查启动终端，稍后刷新或打开帮助。',
    viewer_read_busy:'当前读取过多；暂停自动刷新并关闭重复标签页后重试。',
    viewer_invalid_response:'当前响应格式无法读取；保留已有画面并查看帮助。',
    viewer_layout_timeout:'布局超时；保留画面，可切换执行列表或重试。',
    viewer_layout_busy:'布局任务繁忙；稍后重试。',viewer_layout_size_limit:'该视图超过读取或布局上限；改用更小范围或终端投影。'};
  const key=summaries[code] ?? (status===403?'该观察无法访问；核对当前本机地址与读取权限。':status===409?'观察来源已变化；请重新读取或选择检查点。':status===413?'该视图超过读取或布局上限；改用更小范围或终端投影。':'暂时无法读取此视图；原因尚未确认，请查看帮助。');
  const error = messageError(key); error.code = code; error.helpTopic = topic;
  if (status !== undefined) error.status = status;
  return error;
}
const abortError = () => new DOMException('Read cancelled', 'AbortError');
export function diagnosticTopic(error) {
  if (error?.helpTopic) return error.helpTopic;
  if (error?.status === 403) return 'access';
  if (error?.status === 409) return 'history';
  if (error?.status === 413) return 'layout';
  if ([429, 503].includes(error?.status)) return 'connection';
  if (error?.status >= 500) return 'registry';
  return 'connection';
}
export function createReadRequest(fetcher, {timeoutMs = 15000, maxConcurrent = 8, onError = () => {}} = {}) {
  const active = new Set();
  async function read(path, signal, options = {}) {
    if (signal?.aborted) throw abortError();
    if (active.size >= maxConcurrent) {
      const error = viewerError('viewer_read_busy'); onError(error); throw error;
    }
    const controller = new AbortController(); active.add(controller);
    const fetchSignal = signal && typeof AbortSignal.any === 'function' ? AbortSignal.any([signal,controller.signal]) : controller.signal;
    let deadline, rejectCancelled, timedOut = false;
    const cancelled = new Promise((_, reject) => {rejectCancelled = reject;});
    const abort = () => {controller.abort(); rejectCancelled(timedOut ? viewerError('viewer_read_timeout') : abortError());};
    signal?.addEventListener('abort', abort, {once:true});
    controller.signal.addEventListener('abort', () => rejectCancelled(timedOut ? viewerError('viewer_read_timeout') : abortError()), {once:true});
    deadline = setTimeout(() => {timedOut = true; abort();}, timeoutMs);
    try {
      const work = (async () => {
        const response = await fetcher(path, {...options, cache:'no-store', signal:fetchSignal});
        let value;
        try {value = await response.json();}
        catch {throw viewerError('viewer_invalid_response', 'connection', response.status);}
        if (!response.ok) {
          const code = typeof value?.error === 'string' && /^[a-zA-Z0-9_]{1,80}$/.test(value.error)
            ? value.error : 'viewer_read_failed';
          const error = viewerError(code, undefined, response.status);
          error.helpTopic = diagnosticTopic({status:response.status}); throw error;
        }
        return value;
      })();
      return await Promise.race([work, cancelled]);
    } catch (error) {
      if (error.name !== 'AbortError') onError(error);
      throw error;
    } finally {
      clearTimeout(deadline); signal?.removeEventListener('abort', abort); active.delete(controller);
    }
  }
  read.abortAll = () => {for (const controller of active) controller.abort();};
  read.activeCount = () => active.size;
  return read;
}

/** One worker at a time; timeout cancels its complete generation, never leaves work behind. */
export class BoundedLayoutEngine {
  constructor(factory, {timeoutMs = 15000, maxJobs = 4, maxElements = 20000, maxQueuedElements = 40000} = {}) {
    this.factory = factory; this.timeoutMs = timeoutMs; this.maxJobs = maxJobs;
    this.maxElements = maxElements; this.maxQueuedElements = maxQueuedElements;
    this.worker = null; this.queue = []; this.active = null; this.weight = 0; this.closed = false;
  }
  layout(graph) {
    const weight = (graph.children?.length ?? 0) + (graph.edges?.length ?? 0);
    if (this.closed) return Promise.reject(viewerError('viewer_layout_closed', 'layout'));
    if (weight > this.maxElements) return Promise.reject(viewerError('viewer_layout_size_limit', 'layout'));
    if (this.queue.length + Number(Boolean(this.active)) >= this.maxJobs || this.weight + weight > this.maxQueuedElements)
      return Promise.reject(viewerError('viewer_layout_busy', 'layout'));
    return new Promise((resolve, reject) => {
      this.weight += weight; this.queue.push({graph,weight,resolve,reject}); this.startNext();
    });
  }
  startNext() {
    if (this.active || this.closed || !this.queue.length) return;
    const job = this.active = this.queue.shift();
    job.timer = setTimeout(() => this.reset(viewerError('viewer_layout_timeout', 'layout')), this.timeoutMs);
    Promise.resolve().then(() => {
      if (this.active !== job) return;
      this.worker ??= this.factory(); return this.worker.layout(job.graph);
    }).then(result => {
      if (this.active !== job) return;
      clearTimeout(job.timer); this.active = null; this.weight -= job.weight; job.resolve(result); this.startNext();
    }, error => {
      if (this.active === job) this.reset(viewerError('viewer_layout_failed', 'layout'));
    });
  }
  reset(error = viewerError('viewer_layout_cancelled', 'layout')) {
    const jobs = [...(this.active ? [this.active] : []), ...this.queue];
    this.active = null; this.queue = []; this.weight = 0;
    try {this.worker?.terminateWorker();} catch { /* Still settle and release all jobs. */ }
    this.worker = null;
    for (const job of jobs) {clearTimeout(job.timer); job.reject(error);}
  }
  terminateWorker() {this.closed = true; this.reset(viewerError('viewer_layout_closed', 'layout'));}
}
