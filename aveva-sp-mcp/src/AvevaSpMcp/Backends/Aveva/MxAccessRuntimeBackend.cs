#if MXACCESS
// Compiled only when ArchestrA.MxAccess.dll is found at build time (see AvevaSpMcp.csproj).
//
// STATUS: written from the MXAccess Toolkit documentation without access to the DLL; not yet
// compiled or run against a live platform. Lines marked "VERIFY" are the ones to check first.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using ArchestrA.MxAccess;

namespace AvevaSpMcp.Backends.Aveva
{
    public sealed class MxAccessRuntimeBackend : IRuntimeBackend, IDisposable
    {
        private sealed class Sample
        {
            public object Value; public int Quality; public object Timestamp; public string Status;
        }

        private readonly StaWorker _sta = new StaWorker("mxaccess");
        // Only touched on the STA thread: MXAccess raises its events there while we pump.
        private readonly Dictionary<int, Sample> _samples = new Dictionary<int, Sample>();
        private readonly Dictionary<int, string> _writeResults = new Dictionary<int, string>();
        private LMXProxyServerClass _lmx;
        private int _handle;

        public string Name => "mxaccess";

        public IList<RuntimeValue> Read(IList<string> references, int timeoutMs) => _sta.Invoke(() =>
        {
            Connect();
            var items = new List<KeyValuePair<string, int>>();
            var results = new List<RuntimeValue>();
            try
            {
                foreach (var reference in references)
                {
                    int item = _lmx.AddItem(_handle, reference);
                    items.Add(new KeyValuePair<string, int>(reference, item));
                    _lmx.Advise(_handle, item);
                }
                Pump(timeoutMs, () => items.TrueForAll(i => _samples.ContainsKey(i.Value)));
                foreach (var i in items)
                {
                    results.Add(_samples.TryGetValue(i.Value, out var s)
                        ? new RuntimeValue
                        {
                            Reference = i.Key, Value = s.Value, Quality = QualityText(s.Quality),
                            Timestamp = Convert.ToString(s.Timestamp, System.Globalization.CultureInfo.InvariantCulture), Status = s.Status,
                        }
                        : new RuntimeValue { Reference = i.Key, Quality = "Bad", Status = "No value received within " + timeoutMs + " ms. Is the object deployed and on scan?" });
                }
            }
            finally { Release(items); }
            return (IList<RuntimeValue>)results;
        });

        public OperationResult Write(string reference, string value, int timeoutMs) => _sta.Invoke(() =>
        {
            Connect();
            var items = new List<KeyValuePair<string, int>>();
            try
            {
                int item = _lmx.AddItem(_handle, reference);
                items.Add(new KeyValuePair<string, int>(reference, item));
                _lmx.Advise(_handle, item); // an item must be advised before it can be written
                if (!Pump(timeoutMs, () => _samples.ContainsKey(item)))
                    return new OperationResult { Target = reference, Success = false, Message = "Attribute did not respond; nothing was written." };

                object typed = ValueText.Coerce(value, _samples[item].Value);
                // VERIFY: -1 = no specific user. Attributes secured above "Operate" need a real user id
                // from AuthenticateUser.
                _lmx.Write(_handle, item, typed, -1);
                if (!Pump(timeoutMs, () => _writeResults.ContainsKey(item)))
                    return new OperationResult { Target = reference, Success = false, Message = "Write sent, but no completion was reported within " + timeoutMs + " ms." };
                string status = _writeResults[item];
                return new OperationResult { Target = reference, Success = status == "OK", Message = status == "OK" ? "Written" : status };
            }
            finally { Release(items); }
        });

        private void Connect()
        {
            if (_lmx != null) return;
            var lmx = new LMXProxyServerClass();
            _handle = lmx.Register("aveva-sp-mcp");
            lmx.OnDataChange += OnDataChange;
            lmx.OnWriteComplete += OnWriteComplete;
            _lmx = lmx;
        }

        private void Release(List<KeyValuePair<string, int>> items)
        {
            foreach (var i in items)
            {
                try { _lmx.UnAdvise(_handle, i.Value); _lmx.RemoveItem(_handle, i.Value); }
                catch (Exception) { /* item was never advised */ }
                _samples.Remove(i.Value);
                _writeResults.Remove(i.Value);
            }
        }

        /// <summary>Waits on the STA thread while letting COM deliver MXAccess events to it.</summary>
        private static bool Pump(int timeoutMs, Func<bool> done)
        {
            var clock = Stopwatch.StartNew();
            while (!done())
            {
                if (clock.ElapsedMilliseconds > timeoutMs) return false;
                Thread.CurrentThread.Join(20); // a managed wait on an STA thread pumps COM calls
            }
            return true;
        }

        private void OnDataChange(int hLMXServerHandle, int phItemHandle, object pvItemValue, int pwItemQuality, object pftItemTimeStamp, ref MXSTATUS_PROXY[] pVars)
        {
            _samples[phItemHandle] = new Sample { Value = pvItemValue, Quality = pwItemQuality, Timestamp = pftItemTimeStamp, Status = StatusText(pVars) };
        }

        private void OnWriteComplete(int hLMXServerHandle, int phItemHandle, ref MXSTATUS_PROXY[] pVars)
        {
            _writeResults[phItemHandle] = StatusText(pVars);
        }

        private static string StatusText(MXSTATUS_PROXY[] status)
        {
            if (status == null || status.Length == 0) return "OK";
            var s = status[0];
            return s.success != 0 ? "OK" : "Failed: category " + s.category + ", detected by " + s.detectedBy + ", detail " + s.detail;
        }

        /// <summary>OPC-style quality word: the top two bits of the low byte are good / uncertain / bad.</summary>
        private static string QualityText(int quality)
        {
            switch (quality & 0xC0)
            {
                case 0xC0: return "Good";
                case 0x40: return "Uncertain";
                default: return "Bad";
            }
        }

        public void Dispose()
        {
            try
            {
                _sta.Invoke(() => { if (_lmx != null) { _lmx.Unregister(_handle); _lmx = null; } });
            }
            catch (Exception) { /* shutting down */ }
            _sta.Dispose();
        }
    }
}
#endif
