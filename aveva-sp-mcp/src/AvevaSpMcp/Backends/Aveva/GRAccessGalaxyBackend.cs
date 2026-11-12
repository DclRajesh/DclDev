#if GRACCESS
// Compiled only when ArchestrA.GRAccess.dll is found at build time (see AvevaSpMcp.csproj).
//
// STATUS: written from the GRAccess Toolkit documentation without access to the DLL, so it has
// not been compiled or run against a real Galaxy yet. To keep the first build on a System
// Platform machine painless:
//   * enum members are looked up by name at run time (E<T>("name")), so a wrong member name is a
//     clear run-time error listing the valid names instead of a compile error;
//   * descriptive properties are read through ComProps (case-insensitive, missing = null);
//   * calls whose exact signature should be checked first are marked "VERIFY".
using System;
using System.Collections.Generic;
using System.Linq;
using ArchestrA.GRAccess;

namespace AvevaSpMcp.Backends.Aveva
{
    public sealed class GRAccessGalaxyBackend : IGalaxyBackend, IDisposable
    {
        private readonly StaWorker _sta = new StaWorker("graccess");
        private GRAccessApp _app;
        private IGalaxy _galaxy;

        public string Name => "graccess";
        public bool IsConnected => _galaxy != null;
        public string Node { get; private set; }
        public string Galaxy { get; private set; }
        public string User { get; private set; }

        private GRAccessApp App => _app ?? (_app = new GRAccessAppClass());

        private IGalaxy G => _galaxy ?? throw new GalaxyException("Not connected to a Galaxy.");

        // ---- connection -------------------------------------------------------------------

        public IList<string> ListGalaxies(string node) => _sta.Invoke(() =>
        {
            IGalaxies galaxies = App.QueryGalaxies(node);
            Check(App.CommandResult, "Query galaxies on '" + node + "'");
            var names = new List<string>();
            if (galaxies != null) foreach (IGalaxy g in galaxies) names.Add(g.Name);
            return (IList<string>)names;
        });

        public void Connect(string node, string galaxy, string user, string password) => _sta.Invoke(() =>
        {
            IGalaxies galaxies = App.QueryGalaxies(node);
            Check(App.CommandResult, "Query galaxies on '" + node + "'");
            IGalaxy g = galaxies == null ? null : galaxies[galaxy];
            if (g == null) throw new GalaxyException("Galaxy '" + galaxy + "' not found on node '" + node + "'.");
            g.Login(user ?? "", password ?? "");
            Check(g.CommandResult, "Login to '" + galaxy + "'");
            _galaxy = g; Node = node; Galaxy = galaxy; User = string.IsNullOrEmpty(user) ? "(default)" : user;
        });

        public void Disconnect() => _sta.Invoke(() =>
        {
            var g = _galaxy;
            _galaxy = null;
            if (g != null) try { g.Logout(); } catch (Exception) { /* already gone */ }
        });

        // ---- read -------------------------------------------------------------------------

        public IList<GalaxyObjectInfo> Query(ObjectQuery q) => _sta.Invoke(() =>
        {
            // One condition is evaluated by the Galaxy Repository; the rest are applied here.
            string condition; object value;
            if (!string.IsNullOrEmpty(q.DerivedFrom)) { condition = "derivedOrInstantiatedFrom"; value = q.DerivedFrom; }
            else if (!string.IsNullOrEmpty(q.ContainedBy)) { condition = "containedBy"; value = q.ContainedBy; }
            else if (!string.IsNullOrEmpty(q.Area)) { condition = "belongsToArea"; value = q.Area; }
            else if (!string.IsNullOrEmpty(q.Host)) { condition = "hostEngineIs"; value = q.Host; }
            else { condition = "namedLike"; value = (q.NameLike ?? "%").Replace('*', '%'); }

            var kinds = new List<EgObjectIsTemplateOrInstance>();
            if (q.Kind != "instance") kinds.Add(EgObjectIsTemplateOrInstance.gObjectIsTemplate);
            if (q.Kind != "template") kinds.Add(EgObjectIsTemplateOrInstance.gObjectIsInstance);

            var found = new List<GalaxyObjectInfo>();
            foreach (var kind in kinds)
            {
                // VERIFY: in some toolkit versions the value parameter is "ref object".
                IgObjects result = G.QueryObjects(kind, E<EConditionType>(condition), value, E<EMatch>("MatchCondition"));
                Check(G.CommandResult, "Query objects");
                if (result == null) continue;
                foreach (IgObject o in result) found.Add(Describe(o));
            }

            IEnumerable<GalaxyObjectInfo> r = found;
            if (!string.IsNullOrEmpty(q.NameLike)) { var rx = Wildcard.ToRegex(q.NameLike); r = r.Where(o => rx.IsMatch(o.Tagname ?? "")); }
            if (!string.IsNullOrEmpty(q.ContainedBy)) r = r.Where(o => Same(o.Container, q.ContainedBy));
            if (!string.IsNullOrEmpty(q.Area)) r = r.Where(o => Same(o.Area, q.Area));
            if (!string.IsNullOrEmpty(q.Host)) r = r.Where(o => Same(o.Host, q.Host));
            return (IList<GalaxyObjectInfo>)r.OrderBy(o => o.Tagname, StringComparer.OrdinalIgnoreCase).Take(q.Max).ToList();
        });

        public GalaxyObjectInfo GetObject(string tagname) => _sta.Invoke(() => Describe(Find(tagname)));

        public IList<AttributeInfo> GetAttributes(string tagname, string nameContains, bool configurableOnly) => _sta.Invoke(() =>
        {
            IgObject o = Find(tagname);
            IAttributes attributes = configurableOnly ? o.ConfigurableAttributes : o.Attributes;
            Check(o.CommandResult, "Read attributes of '" + tagname + "'");
            var list = new List<AttributeInfo>();
            foreach (IAttribute a in attributes)
            {
                string name = a.Name;
                if (!string.IsNullOrEmpty(nameContains) && name.IndexOf(nameContains, StringComparison.OrdinalIgnoreCase) < 0) continue;
                list.Add(new AttributeInfo
                {
                    Name = name,
                    DataType = ComProps.Text(a, typeof(IAttribute), "DataType"),
                    Value = ReadValue(ComProps.Get(a, typeof(IAttribute), "value") as MxValue),
                    Category = ComProps.Text(a, typeof(IAttribute), "AttributeCategory"),
                    Security = ComProps.Text(a, typeof(IAttribute), "SecurityClassification"),
                    Locked = ComProps.Text(a, typeof(IAttribute), "Locked"),
                    Description = ComProps.Text(a, typeof(IAttribute), "Description"),
                });
            }
            return (IList<AttributeInfo>)list.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToList();
        });

        // ---- configure --------------------------------------------------------------------

        public GalaxyObjectInfo DeriveTemplate(string parentTemplate, string newName) => _sta.Invoke(() =>
        {
            var parent = (ITemplate)Find(parentTemplate, mustBeTemplate: true);
            ITemplate created = parent.CreateTemplate(newName, true);
            Check(parent.CommandResult, "Derive '" + newName + "' from '" + parentTemplate + "'");
            return Describe((IgObject)created);
        });

        public GalaxyObjectInfo CreateInstance(string template, string newName) => _sta.Invoke(() =>
        {
            var parent = (ITemplate)Find(template, mustBeTemplate: true);
            IInstance created = parent.CreateInstance(newName, true);
            Check(parent.CommandResult, "Create instance '" + newName + "' of '" + template + "'");
            return Describe((IgObject)created);
        });

        public void CheckOut(string tagname) => _sta.Invoke(() => DoCheckOut(Find(tagname), tagname));

        public void CheckIn(string tagname, string comment) => _sta.Invoke(() =>
        {
            IgObject o = Find(tagname);
            o.CheckIn(comment ?? "");
            Check(o.CommandResult, "Check in '" + tagname + "'");
        });

        public void UndoCheckOut(string tagname) => _sta.Invoke(() =>
        {
            IgObject o = Find(tagname);
            o.UndoCheckOut();
            Check(o.CommandResult, "Undo check-out of '" + tagname + "'");
        });

        public void SetAttributes(string tagname, IDictionary<string, string> values, string comment) => _sta.Invoke(() =>
            Edit(tagname, comment, o => SetValues(o, tagname, values)));

        public void AddUda(string tagname, UdaSpec uda, string comment) => _sta.Invoke(() =>
            Edit(tagname, comment, o =>
            {
                o.AddUDA(uda.Name, E<MxDataType>(uda.DataType), E<MxAttributeCategory>(uda.Category),
                    E<MxSecurityClassification>(uda.Security), uda.IsArray, uda.ArrayLength);
                Check(o.CommandResult, "Add UDA '" + uda.Name + "'");
                if (uda.InitialValue != null)
                {
                    Save(o, tagname); // the new attribute only shows up after a save
                    SetValues(o, tagname, new Dictionary<string, string> { { uda.Name, uda.InitialValue } });
                }
            }));

        public void SetScript(string tagname, ScriptSpec script, string comment) => _sta.Invoke(() =>
            Edit(tagname, comment, o =>
            {
                if (FindAttribute(o.Attributes, script.Name + ".ExecuteText") == null)
                {
                    // VERIFY: "ScriptExtension" is the extension primitive name used by the IDE's Scripts tab.
                    o.AddExtensionPrimitive("ScriptExtension", script.Name, true);
                    Check(o.CommandResult, "Add script '" + script.Name + "'");
                    Save(o, tagname);
                }
                SetValues(o, tagname, script.ToAttributes());
            }));

        public void Assign(string tagname, string area, string container, string host, string comment) => _sta.Invoke(() =>
            Edit(tagname, comment, o =>
            {
                if (!(o is IInstance)) throw new GalaxyException("Only instances can be assigned.");
                // VERIFY: Area / Container / Host are writable string properties on IInstance.
                if (area != null) ComProps.Set(o, typeof(IInstance), "Area", area);
                if (container != null) ComProps.Set(o, typeof(IInstance), "Container", container);
                if (host != null) ComProps.Set(o, typeof(IInstance), "Host", host);
                Check(o.CommandResult, "Assign '" + tagname + "'");
            }));

        public void Delete(string tagname) => _sta.Invoke(() =>
        {
            IgObject o = Find(tagname);
            if (o is ITemplate t) t.DeleteTemplate(E<EForceDeleteTemplateOption>("dontForceTemplateDelete"));
            else ((IInstance)o).DeleteInstance(E<EForceDeleteInstanceOption>("dontForceInstanceDelete"));
            Check(o.CommandResult, "Delete '" + tagname + "'");
        });

        // ---- deploy -----------------------------------------------------------------------

        public IList<OperationResult> Deploy(IList<string> tagnames) => _sta.Invoke(() => ForEachInstance(tagnames, "Deployed", i =>
            i.Deploy(
                E<EActionForCurrentlyDeployedObjects>("deployChanges"),
                E<ESkipIfCurrentlyUndeployed>("dontSkipIfCurrentlyUndeployed"),
                E<EDeployOnScan>("doDeployOnScan"),
                E<EForceOffScan>("doForceOffScan"),
                E<EMarkAsDeployedOnStatusMismatch>("doMarkAsDeployedOnStatusMismatch"))));

        public IList<OperationResult> Undeploy(IList<string> tagnames) => _sta.Invoke(() => ForEachInstance(tagnames, "Undeployed", i =>
            i.Undeploy(E<EForceOffScan>("doForceOffScan"), E<EMarkAsUndeployedOnStatusMismatch>("doMarkAsUndeployedOnStatusMismatch"))));

        private IList<OperationResult> ForEachInstance(IList<string> tagnames, string done, Action<IInstance> action)
        {
            var results = new List<OperationResult>();
            foreach (var tag in tagnames)
            {
                try
                {
                    var instance = Find(tag) as IInstance ?? throw new GalaxyException("'" + tag + "' is a template; only instances can be deployed.");
                    action(instance);
                    Check(instance.CommandResult, done + " '" + tag + "'");
                    results.Add(new OperationResult { Target = tag, Success = true, Message = done });
                }
                catch (Exception e) when (e is GalaxyException || e is System.Runtime.InteropServices.COMException)
                {
                    results.Add(new OperationResult { Target = tag, Success = false, Message = e.Message });
                }
            }
            return results;
        }

        // ---- import / export --------------------------------------------------------------

        public string Export(IList<string> tagnames, string format, string path) => _sta.Invoke(() =>
        {
            IgObjects set = G.CreategObjectCollection();
            foreach (var tag in tagnames) set.Add(Find(tag));
            set.ExportObjects(E<EExportType>(format == "csv" ? "exportAsCSV" : "exportAsPDF"), path);
            Check(set.CommandResults, "Export");
            return path;
        });

        public void ImportPackage(string path, bool overwrite) => _sta.Invoke(() =>
        {
            G.ImportObjects(path, overwrite);
            Check(G.CommandResult, "Import '" + path + "'");
        });

        public void LoadCsv(string path, string mode) => _sta.Invoke(() =>
        {
            G.GRLoad(path, E<GRLoadMode>(mode == "update" ? "GRLoadModeUpdate" : "GRLoadModeSkip"));
            Check(G.CommandResult, "Load '" + path + "'");
        });

        // ---- helpers ----------------------------------------------------------------------

        private IgObject Find(string tagname, bool mustBeTemplate = false)
        {
            if (string.IsNullOrWhiteSpace(tagname)) throw new GalaxyException("A tagname is required.");
            bool isTemplate = tagname.StartsWith("$");
            if (mustBeTemplate && !isTemplate) throw new GalaxyException("'" + tagname + "' is not a template (template names start with '$').");
            var names = new[] { tagname };
            IgObjects result = G.QueryObjectsByName(
                isTemplate ? EgObjectIsTemplateOrInstance.gObjectIsTemplate : EgObjectIsTemplateOrInstance.gObjectIsInstance, ref names);
            Check(G.CommandResult, "Look up '" + tagname + "'");
            if (result != null) foreach (IgObject o in result) return o;
            throw new GalaxyException("Object '" + tagname + "' not found in the Galaxy.");
        }

        /// <summary>Check out, apply the edit, save and check in. Any failure undoes the check-out.</summary>
        private void Edit(string tagname, string comment, Action<IgObject> edit)
        {
            IgObject o = Find(tagname);
            DoCheckOut(o, tagname);
            try
            {
                edit(o);
                Save(o, tagname);
                o.CheckIn(comment ?? "");
                Check(o.CommandResult, "Check in '" + tagname + "'");
            }
            catch
            {
                try { o.UndoCheckOut(); } catch (Exception) { /* report the original error */ }
                throw;
            }
        }

        private static void DoCheckOut(IgObject o, string tagname)
        {
            o.CheckOut();
            Check(o.CommandResult, "Check out '" + tagname + "'");
        }

        private static void Save(IgObject o, string tagname)
        {
            o.Save();
            Check(o.CommandResult, "Save '" + tagname + "'");
        }

        private static void SetValues(IgObject o, string tagname, IDictionary<string, string> values)
        {
            IAttributes attributes = o.ConfigurableAttributes;
            foreach (var kv in values)
            {
                IAttribute a = FindAttribute(attributes, kv.Key)
                    ?? throw new GalaxyException("'" + tagname + "' has no configurable attribute '" + kv.Key + "'.");
                a.SetValue(BuildValue(a, kv.Value));
                Check(a.CommandResult, "Set " + tagname + "." + kv.Key);
            }
        }

        private static IAttribute FindAttribute(IAttributes attributes, string name)
        {
            try { return attributes[name]; }
            catch (Exception) { return null; }
        }

        private static MxValue BuildValue(IAttribute a, string text)
        {
            string type = ComProps.Text(a, typeof(IAttribute), "DataType") ?? "";
            MxValue v = new MxValueClass();
            switch (type)
            {
                case "MxBoolean": v.PutBoolean((bool)ValueText.Parse(text, type)); break;
                case "MxInteger": v.PutInteger((int)ValueText.Parse(text, type)); break;
                case "MxFloat": v.PutFloat((float)ValueText.Parse(text, type)); break;
                case "MxDouble": v.PutDouble((double)ValueText.Parse(text, type)); break;
                case "MxString":
                case "MxBigString":
                case "MxInternationalizedString":
                    v.PutString(text ?? ""); break;
                case "MxReferenceType":
                    // VERIFY: references (e.g. PV.InputSource) are edited through IMxReference.
                    IMxReference reference = ((MxValue)ComProps.Get(a, typeof(IAttribute), "value")).GetMxReference();
                    reference.FullReferenceString = text ?? "";
                    v.PutMxReference(reference);
                    break;
                default:
                    throw new GalaxyException("Setting attributes of type " + type + " is not supported yet (attribute '" + a.Name + "').");
            }
            return v;
        }

        private static object ReadValue(MxValue v)
        {
            if (v == null) return null;
            try
            {
                switch (v.GetDataType().ToString())
                {
                    case "MxBoolean": return v.GetBoolean();
                    case "MxInteger": return v.GetInteger();
                    case "MxFloat": return v.GetFloat();
                    case "MxDouble": return v.GetDouble();
                    case "MxReferenceType": return v.GetMxReference().FullReferenceString;
                    case "MxNoData": return null;
                    default: return v.GetString();
                }
            }
            catch (Exception)
            {
                // Arrays and a few exotic types cannot be read as a scalar; see README "Known gaps".
                return "<not readable as a scalar>";
            }
        }

        private static GalaxyObjectInfo Describe(IgObject o)
        {
            bool isTemplate = o is ITemplate;
            Type t = typeof(IgObject);
            return new GalaxyObjectInfo
            {
                Tagname = o.Tagname,
                Kind = isTemplate ? "template" : "instance",
                DerivedFrom = ComProps.Text(o, t, "DerivedFrom"),
                BasedOn = ComProps.Text(o, t, "BasedOn"),
                ContainedName = ComProps.Text(o, t, "ContainedName"),
                HierarchicalName = ComProps.Text(o, t, "HierarchicalName"),
                Container = ComProps.Text(o, t, "Container"),
                Area = ComProps.Text(o, t, "Area"),
                Host = ComProps.Text(o, t, "Host"),
                CheckoutStatus = ComProps.Text(o, t, "CheckoutStatus"),
                CheckedOutBy = ComProps.Text(o, t, "CheckedOutBy"),
                ConfigVersion = ComProps.Text(o, t, "ConfigVersion"),
                DeploymentStatus = isTemplate ? null : ComProps.Text(o, typeof(IInstance), "DeploymentStatus"),
            };
        }

        private static void Check(ICommandResult result, string what)
        {
            if (result == null || result.Successful) return;
            throw new GalaxyException(what + " failed: " + Join(result.Text, result.CustomMessage) + " (" + result.ID + ")");
        }

        private static void Check(ICommandResults results, string what)
        {
            if (results == null) return;
            foreach (ICommandResult r in results) Check(r, what);
        }

        private static string Join(params string[] parts) => string.Join(" ", parts.Where(p => !string.IsNullOrWhiteSpace(p)));

        private static bool Same(string a, string b) => string.Equals(a, b, StringComparison.OrdinalIgnoreCase);

        /// <summary>Enum member by name, so a toolkit-version difference is a clear message, not a build break.</summary>
        private static T E<T>(string name) where T : struct
        {
            if (Enum.TryParse(name, true, out T value) && Enum.IsDefined(typeof(T), value)) return value;
            throw new GalaxyException(typeof(T).Name + " has no member '" + name + "'. Valid: " + string.Join(", ", Enum.GetNames(typeof(T))));
        }

        public void Dispose()
        {
            Disconnect();
            _sta.Dispose();
        }
    }
}
#endif
