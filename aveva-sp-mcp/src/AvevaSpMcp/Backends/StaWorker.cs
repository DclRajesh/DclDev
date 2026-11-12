using System;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

namespace AvevaSpMcp.Backends
{
    /// <summary>
    /// Runs every call on one dedicated thread. GRAccess and MXAccess are apartment-threaded COM
    /// components: they must be created and used on the same STA thread, and MCP tool calls arrive
    /// on arbitrary thread-pool threads.
    /// </summary>
    public sealed class StaWorker : IDisposable
    {
        private readonly BlockingCollection<Action> _queue = new BlockingCollection<Action>();
        private readonly Thread _thread;

        public StaWorker(string name)
        {
            _thread = new Thread(Loop) { IsBackground = true, Name = name };
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                _thread.SetApartmentState(ApartmentState.STA);
            _thread.Start();
        }

        public T Invoke<T>(Func<T> func)
        {
            if (Thread.CurrentThread == _thread) return func();
            var done = new TaskCompletionSource<T>();
            _queue.Add(() =>
            {
                try { done.SetResult(func()); }
                catch (Exception e) { done.SetException(e); }
            });
            return done.Task.GetAwaiter().GetResult();
        }

        public void Invoke(Action action) => Invoke<object>(() => { action(); return null; });

        private void Loop()
        {
            foreach (var work in _queue.GetConsumingEnumerable()) work();
        }

        public void Dispose() => _queue.CompleteAdding();
    }
}
