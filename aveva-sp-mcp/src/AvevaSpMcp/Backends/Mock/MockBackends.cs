using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace AvevaSpMcp.Backends.Mock
{
    /// <summary>
    /// A small in-memory Galaxy. It lets the MCP tools be exercised (and tested) on a machine with
    /// no System Platform installed. It imitates the check-out / check-in / deploy rules loosely;
    /// it is not a simulator.
    /// </summary>
    public sealed class MockGalaxyBackend : IGalaxyBackend
    {
        private sealed class Obj
        {
            public GalaxyObjectInfo Info;
            public readonly Dictionary<string, AttributeInfo> Attributes =
                new Dictionary<string, AttributeInfo>(StringComparer.OrdinalIgnoreCase);
        }

        private readonly Dictionary<string, Obj> _objects = new Dictionary<string, Obj>(StringComparer.OrdinalIgnoreCase);
        private readonly object _lock = new object();

        public const string GalaxyName = "MockGalaxy";

        public string Name => "mock";
        public bool IsConnected { get; private set; }
        public string Node { get; private set; }
        public string Galaxy { get; private set; }
        public string User { get; private set; }

        public MockGalaxyBackend()
        {
            foreach (var t in new[] { "$UserDefined", "$Area", "$AppEngine", "$WinPlatform", "$AnalogDevice", "$DiscreteDevice" })
                AddObject(t, null, t);

            AddObject("$Pump", "$UserDefined", "$UserDefined");
            Uda("$Pump", "Running", "MxBoolean", false);
            Uda("$Pump", "SpeedSP", "MxFloat", 0.0f);
            Uda("$Pump", "RunHours", "MxDouble", 0.0);
            ScriptAttrs("$Pump", "RunHoursCalc", "Me.RunHours = Me.RunHours + 1.0 / 3600.0;", "Me.Running", "WhileTrue");

            AddObject("Platform_001", "$WinPlatform", "$WinPlatform");
            AddObject("AppEngine_001", "$AppEngine", "$AppEngine").Info.Host = "Platform_001";
            AddObject("Area_PumpStation", "$Area", "$Area").Info.Host = "AppEngine_001";
            foreach (var p in new[] { "Pump_001", "Pump_002" })
            {
                var o = AddObject(p, "$Pump", "$UserDefined");
                o.Info.Area = "Area_PumpStation";
                o.Info.Host = "AppEngine_001";
            }
            foreach (var deployed in new[] { "Platform_001", "AppEngine_001", "Area_PumpStation", "Pump_001" })
                _objects[deployed].Info.DeploymentStatus = "Deployed";
        }

        // ---- connection -------------------------------------------------------------------

        public IList<string> ListGalaxies(string node) => new List<string> { GalaxyName };

        public void Connect(string node, string galaxy, string user, string password)
        {
            if (!string.Equals(galaxy, GalaxyName, StringComparison.OrdinalIgnoreCase))
                throw new GalaxyException("Galaxy '" + galaxy + "' not found on node '" + node + "'. The mock backend only has '" + GalaxyName + "'.");
            Node = node; Galaxy = GalaxyName; User = string.IsNullOrEmpty(user) ? "DefaultUser" : user;
            IsConnected = true;
        }

        public void Disconnect() { IsConnected = false; }

        // ---- read -------------------------------------------------------------------------

        public IList<GalaxyObjectInfo> Query(ObjectQuery q)
        {
            lock (_lock)
            {
                IEnumerable<Obj> r = _objects.Values;
                if (q.Kind == "template" || q.Kind == "instance") r = r.Where(o => o.Info.Kind == q.Kind);
                if (!string.IsNullOrEmpty(q.NameLike)) { var rx = Wildcard.ToRegex(q.NameLike); r = r.Where(o => rx.IsMatch(o.Info.Tagname)); }
                if (!string.IsNullOrEmpty(q.DerivedFrom)) r = r.Where(o => IsDerivedFrom(o, q.DerivedFrom));
                if (!string.IsNullOrEmpty(q.ContainedBy)) r = r.Where(o => Same(o.Info.Container, q.ContainedBy));
                if (!string.IsNullOrEmpty(q.Area)) r = r.Where(o => Same(o.Info.Area, q.Area));
                if (!string.IsNullOrEmpty(q.Host)) r = r.Where(o => Same(o.Info.Host, q.Host));
                return r.OrderBy(o => o.Info.Tagname, StringComparer.OrdinalIgnoreCase).Take(q.Max).Select(o => Copy(o.Info)).ToList();
            }
        }

        public GalaxyObjectInfo GetObject(string tagname) { lock (_lock) return Copy(Find(tagname).Info); }

        public IList<AttributeInfo> GetAttributes(string tagname, string nameContains, bool configurableOnly)
        {
            lock (_lock)
            {
                return Find(tagname).Attributes.Values
                    .Where(a => string.IsNullOrEmpty(nameContains) || a.Name.IndexOf(nameContains, StringComparison.OrdinalIgnoreCase) >= 0)
                    .OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase)
                    .Select(Copy).ToList();
            }
        }

        // ---- configure --------------------------------------------------------------------

        public GalaxyObjectInfo DeriveTemplate(string parentTemplate, string newName)
        {
            lock (_lock)
            {
                var parent = Find(parentTemplate);
                if (parent.Info.Kind != "template") throw new GalaxyException("'" + parentTemplate + "' is not a template.");
                if (!newName.StartsWith("$")) throw new GalaxyException("Template names start with '$'.");
                return Copy(AddObject(newName, parent.Info.Tagname, parent.Info.BasedOn).Info);
            }
        }

        public GalaxyObjectInfo CreateInstance(string template, string newName)
        {
            lock (_lock)
            {
                var parent = Find(template);
                if (parent.Info.Kind != "template") throw new GalaxyException("'" + template + "' is not a template.");
                if (newName.StartsWith("$")) throw new GalaxyException("Instance names cannot start with '$'.");
                return Copy(AddObject(newName, parent.Info.Tagname, parent.Info.BasedOn).Info);
            }
        }

        public void CheckOut(string tagname)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                if (o.Info.CheckoutStatus != "notCheckedOut")
                    throw new GalaxyException("'" + tagname + "' is already checked out by " + o.Info.CheckedOutBy + ".");
                o.Info.CheckoutStatus = "checkedOutToMe";
                o.Info.CheckedOutBy = User;
            }
        }

        public void CheckIn(string tagname, string comment)
        {
            lock (_lock)
            {
                var o = RequireCheckedOut(tagname);
                o.Info.CheckoutStatus = "notCheckedOut";
                o.Info.CheckedOutBy = null;
                o.Info.ConfigVersion = (int.Parse(o.Info.ConfigVersion, CultureInfo.InvariantCulture) + 1).ToString(CultureInfo.InvariantCulture);
                if (o.Info.DeploymentStatus == "Deployed") o.Info.DeploymentStatus = "DeployedWithPendingChanges";
            }
        }

        public void UndoCheckOut(string tagname)
        {
            lock (_lock)
            {
                var o = RequireCheckedOut(tagname);
                o.Info.CheckoutStatus = "notCheckedOut";
                o.Info.CheckedOutBy = null;
            }
        }

        public void SetAttributes(string tagname, IDictionary<string, string> values, string comment)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                // Validate everything first so a bad name or value changes nothing.
                var parsed = new List<KeyValuePair<AttributeInfo, object>>();
                foreach (var kv in values)
                {
                    if (!o.Attributes.TryGetValue(kv.Key, out var a))
                        throw new GalaxyException("'" + tagname + "' has no attribute '" + kv.Key + "'.");
                    parsed.Add(new KeyValuePair<AttributeInfo, object>(a, ValueText.Parse(kv.Value, a.DataType)));
                }
                CheckOut(tagname);
                foreach (var p in parsed) p.Key.Value = p.Value;
                CheckIn(tagname, comment);
            }
        }

        public void AddUda(string tagname, UdaSpec uda, string comment)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                if (o.Attributes.ContainsKey(uda.Name)) throw new GalaxyException("'" + tagname + "' already has an attribute '" + uda.Name + "'.");
                var initial = uda.InitialValue != null ? ValueText.Parse(uda.InitialValue, uda.DataType) : ValueText.Default(uda.DataType);
                CheckOut(tagname);
                Uda(tagname, uda.Name, uda.DataType, initial, uda.Category, uda.Security);
                CheckIn(tagname, comment);
            }
        }

        public void SetScript(string tagname, ScriptSpec script, string comment)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                CheckOut(tagname);
                if (!o.Attributes.ContainsKey(script.Name + ".ExecuteText")) ScriptAttrs(tagname, script.Name, "", "", "WhileTrue");
                foreach (var kv in script.ToAttributes()) o.Attributes[kv.Key].Value = kv.Value;
                CheckIn(tagname, comment);
            }
        }

        public void Assign(string tagname, string area, string container, string host, string comment)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                if (o.Info.Kind != "instance") throw new GalaxyException("Only instances can be assigned.");
                foreach (var target in new[] { area, container, host })
                    if (!string.IsNullOrEmpty(target)) Find(target);
                CheckOut(tagname);
                if (area != null) o.Info.Area = area;
                if (container != null) o.Info.Container = container;
                if (host != null) o.Info.Host = host;
                CheckIn(tagname, comment);
            }
        }

        public void Delete(string tagname)
        {
            lock (_lock)
            {
                var o = Find(tagname);
                if (o.Info.DeploymentStatus != null && o.Info.DeploymentStatus != "Undeployed")
                    throw new GalaxyException("'" + tagname + "' is deployed. Undeploy it first.");
                var child = _objects.Values.FirstOrDefault(c => Same(c.Info.DerivedFrom, o.Info.Tagname));
                if (child != null) throw new GalaxyException("'" + tagname + "' still has derived objects (e.g. " + child.Info.Tagname + ").");
                _objects.Remove(o.Info.Tagname);
            }
        }

        // ---- deploy -----------------------------------------------------------------------

        public IList<OperationResult> Deploy(IList<string> tagnames) => ForEach(tagnames, o =>
        {
            if (o.Info.Kind != "instance") throw new GalaxyException("Only instances can be deployed.");
            if (o.Info.CheckoutStatus != "notCheckedOut") throw new GalaxyException("Object is checked out.");
            if (!string.IsNullOrEmpty(o.Info.Host) && Find(o.Info.Host).Info.DeploymentStatus == "Undeployed")
                throw new GalaxyException("Host '" + o.Info.Host + "' is not deployed.");
            o.Info.DeploymentStatus = "Deployed";
            return "Deployed";
        });

        public IList<OperationResult> Undeploy(IList<string> tagnames) => ForEach(tagnames, o =>
        {
            if (o.Info.Kind != "instance") throw new GalaxyException("Only instances can be undeployed.");
            o.Info.DeploymentStatus = "Undeployed";
            return "Undeployed";
        });

        private IList<OperationResult> ForEach(IList<string> tagnames, Func<Obj, string> action)
        {
            var results = new List<OperationResult>();
            lock (_lock)
            {
                foreach (var t in tagnames)
                {
                    try { results.Add(new OperationResult { Target = t, Success = true, Message = action(Find(t)) }); }
                    catch (GalaxyException e) { results.Add(new OperationResult { Target = t, Success = false, Message = e.Message }); }
                }
            }
            return results;
        }

        // ---- import / export --------------------------------------------------------------

        public string Export(IList<string> tagnames, string format, string path)
        {
            lock (_lock)
            {
                var lines = new List<string> { "; mock export (" + format + ") - not a real AVEVA file" };
                foreach (var t in tagnames)
                {
                    var o = Find(t);
                    lines.Add(":TEMPLATE=" + o.Info.DerivedFrom);
                    lines.Add(o.Info.Tagname + "," + string.Join(",", o.Attributes.Values.Select(a => a.Name + "=" + Convert.ToString(a.Value, CultureInfo.InvariantCulture))));
                }
                File.WriteAllLines(path, lines);
                return path;
            }
        }

        public void ImportPackage(string path, bool overwrite) => RequireFile(path);
        public void LoadCsv(string path, string mode) => RequireFile(path);

        private static void RequireFile(string path)
        {
            if (!File.Exists(path)) throw new GalaxyException("File not found: " + path);
            throw new GalaxyException("The mock backend cannot import files. Use the GRAccess backend on a System Platform machine.");
        }

        // ---- helpers ----------------------------------------------------------------------

        private Obj Find(string tagname)
        {
            if (tagname != null && _objects.TryGetValue(tagname, out var o)) return o;
            throw new GalaxyException("Object '" + tagname + "' not found in the Galaxy.");
        }

        private Obj RequireCheckedOut(string tagname)
        {
            var o = Find(tagname);
            if (o.Info.CheckoutStatus == "notCheckedOut") throw new GalaxyException("'" + tagname + "' is not checked out.");
            return o;
        }

        private bool IsDerivedFrom(Obj o, string ancestor)
        {
            for (var p = o.Info.DerivedFrom; !string.IsNullOrEmpty(p); p = _objects.TryGetValue(p, out var po) ? po.Info.DerivedFrom : null)
                if (Same(p, ancestor)) return true;
            return false;
        }

        private Obj AddObject(string tagname, string derivedFrom, string basedOn)
        {
            if (string.IsNullOrWhiteSpace(tagname)) throw new GalaxyException("A name is required.");
            if (_objects.ContainsKey(tagname)) throw new GalaxyException("An object named '" + tagname + "' already exists.");
            var isTemplate = tagname.StartsWith("$");
            var o = new Obj
            {
                Info = new GalaxyObjectInfo
                {
                    Tagname = tagname,
                    Kind = isTemplate ? "template" : "instance",
                    DerivedFrom = derivedFrom,
                    BasedOn = basedOn,
                    ContainedName = "",
                    HierarchicalName = tagname,
                    CheckoutStatus = "notCheckedOut",
                    ConfigVersion = "1",
                    DeploymentStatus = isTemplate ? null : "Undeployed",
                }
            };
            _objects[tagname] = o;
            Attr(o, "ShortDesc", "MxString", "");
            if (derivedFrom != null && _objects.TryGetValue(derivedFrom, out var parent))
                foreach (var a in parent.Attributes.Values) o.Attributes[a.Name] = Copy(a);
            return o;
        }

        private void Uda(string tag, string name, string type, object value,
            string category = "MxCategoryWriteable_USC_Lockable", string security = "MxSecurityOperate")
        {
            var a = Attr(_objects[tag], name, type, value);
            a.Category = category; a.Security = security;
        }

        private void ScriptAttrs(string tag, string name, string execute, string expression, string trigger)
        {
            var o = _objects[tag];
            foreach (var part in new[] { "DeclarationsText", "StartupText", "OnScanText", "OffScanText", "ShutdownText" })
                Attr(o, name + "." + part, "MxBigString", "");
            Attr(o, name + ".ExecuteText", "MxBigString", execute);
            Attr(o, name + ".Expression", "MxString", expression);
            Attr(o, name + ".TriggerType", "MxString", trigger);
        }

        private static AttributeInfo Attr(Obj o, string name, string type, object value)
        {
            var a = new AttributeInfo { Name = name, DataType = type, Value = value, Category = "MxCategoryWriteable_C_Lockable", Security = "MxSecurityConfigure", Locked = "MxUnLocked" };
            o.Attributes[name] = a;
            return a;
        }

        private static bool Same(string a, string b) => string.Equals(a, b, StringComparison.OrdinalIgnoreCase);

        private static GalaxyObjectInfo Copy(GalaxyObjectInfo i) => (GalaxyObjectInfo)CloneHelper.Clone(i);
        private static AttributeInfo Copy(AttributeInfo a) => (AttributeInfo)CloneHelper.Clone(a);
    }

    /// <summary>Returns values for deployed mock objects; writes are remembered in memory.</summary>
    public sealed class MockRuntimeBackend : IRuntimeBackend
    {
        private readonly IGalaxyBackend _galaxy;
        private readonly Dictionary<string, object> _written = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

        public MockRuntimeBackend(IGalaxyBackend galaxy) { _galaxy = galaxy; }
        public string Name => "mock";

        public IList<RuntimeValue> Read(IList<string> references, int timeoutMs) => references.Select(ReadOne).ToList();

        public OperationResult Write(string reference, string value, int timeoutMs)
        {
            var current = ReadOne(reference);
            if (current.Quality != "Good") return new OperationResult { Target = reference, Success = false, Message = current.Status };
            lock (_written) _written[reference] = ValueText.Coerce(value, current.Value);
            return new OperationResult { Target = reference, Success = true, Message = "Written" };
        }

        private RuntimeValue ReadOne(string reference)
        {
            var bad = new RuntimeValue { Reference = reference, Quality = "Bad" };
            var dot = reference == null ? -1 : reference.IndexOf('.');
            if (dot <= 0) { bad.Status = "Use Tagname.Attribute, e.g. Pump_001.SpeedSP"; return bad; }
            try
            {
                var obj = _galaxy.GetObject(reference.Substring(0, dot));
                if (obj.DeploymentStatus == null || obj.DeploymentStatus == "Undeployed") { bad.Status = "Object is not deployed"; return bad; }
                var attr = _galaxy.GetAttributes(obj.Tagname, null, false)
                    .FirstOrDefault(a => string.Equals(a.Name, reference.Substring(dot + 1), StringComparison.OrdinalIgnoreCase));
                if (attr == null) { bad.Status = "No such attribute"; return bad; }
                object value;
                lock (_written) if (!_written.TryGetValue(reference, out value)) value = attr.Value;
                return new RuntimeValue { Reference = reference, Value = value, Quality = "Good", Timestamp = DateTime.UtcNow.ToString("o"), Status = "OK" };
            }
            catch (GalaxyException e) { bad.Status = e.Message; return bad; }
        }
    }

    internal static class CloneHelper
    {
        public static object Clone(object source)
        {
            var copy = Activator.CreateInstance(source.GetType());
            foreach (var p in source.GetType().GetProperties()) p.SetValue(copy, p.GetValue(source));
            return copy;
        }
    }
}

namespace AvevaSpMcp.Backends
{
    public static class Wildcard
    {
        /// <summary>Turns a tagname pattern into a regex. % and * match any text; nothing else is special.</summary>
        public static Regex ToRegex(string pattern)
        {
            var body = Regex.Escape(pattern).Replace("%", ".*").Replace("\\*", ".*");
            return new Regex("^" + body + "$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        }
    }

    /// <summary>Converts the text an MCP client sends into a typed attribute value.</summary>
    public static class ValueText
    {
        public static object Parse(string text, string mxDataType)
        {
            try
            {
                switch ((mxDataType ?? "").ToLowerInvariant())
                {
                    case "mxboolean": return ParseBool(text);
                    case "mxinteger": return int.Parse(text, NumberStyles.Integer, CultureInfo.InvariantCulture);
                    case "mxfloat": return float.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
                    case "mxdouble": return double.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
                    default: return text;
                }
            }
            catch (Exception e) when (e is FormatException || e is OverflowException)
            {
                throw new GalaxyException("'" + text + "' is not a valid " + mxDataType + " value.");
            }
        }

        public static object Default(string mxDataType)
        {
            switch ((mxDataType ?? "").ToLowerInvariant())
            {
                case "mxboolean": return false;
                case "mxinteger": return 0;
                case "mxfloat": return 0f;
                case "mxdouble": return 0d;
                default: return "";
            }
        }

        /// <summary>Converts text to the type of an existing value (used for run-time writes).</summary>
        public static object Coerce(string text, object like)
        {
            if (like is bool) return Parse(text, "MxBoolean");
            if (like is int || like is short || like is long) return Parse(text, "MxInteger");
            if (like is float) return Parse(text, "MxFloat");
            if (like is double) return Parse(text, "MxDouble");
            return text;
        }

        private static bool ParseBool(string text)
        {
            switch ((text ?? "").Trim().ToLowerInvariant())
            {
                case "true": case "1": case "on": return true;
                case "false": case "0": case "off": return false;
                default: throw new FormatException();
            }
        }
    }
}
