using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using AvevaSpMcp.Backends;
using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace AvevaSpMcp.Tools
{
    /// <summary>
    /// Shared plumbing for tool classes: lazy connection to the configured Galaxy, and turning
    /// backend errors into messages the MCP client is shown.
    /// </summary>
    public class GalaxySession
    {
        private readonly object _lock = new object();
        public IGalaxyBackend Backend { get; }
        public IRuntimeBackend Runtime { get; }
        public ServerOptions Options { get; }
        public AccessPolicy Policy { get; }

        public GalaxySession(IGalaxyBackend backend, IRuntimeBackend runtime, ServerOptions options, AccessPolicy policy)
        {
            Backend = backend; Runtime = runtime; Options = options; Policy = policy;
        }

        /// <summary>The backend, connected. Connects to AVEVA_SP_GALAXY on first use when it is set.</summary>
        public IGalaxyBackend Galaxy()
        {
            lock (_lock)
            {
                if (Backend.IsConnected) return Backend;
                if (string.IsNullOrEmpty(Options.Galaxy))
                    throw new GalaxyException("Not connected to a Galaxy. Call galaxy_list to see what is available, then galaxy_connect.");
                Backend.Connect(Options.Node, Options.Galaxy, Options.User, Options.Password);
                return Backend;
            }
        }

        public ConnectionInfo Status() => new ConnectionInfo
        {
            Connected = Backend.IsConnected,
            Backend = Backend.Name + " (runtime: " + Runtime.Name + ")",
            Node = Backend.IsConnected ? Backend.Node : Options.Node,
            Galaxy = Backend.IsConnected ? Backend.Galaxy : Options.Galaxy,
            User = Backend.IsConnected ? Backend.User : null,
            Permissions = Policy.Granted(),
            TransferDirectory = Options.TransferDirectory,
        };

        public static T Run<T>(Func<T> action)
        {
            try { return action(); }
            catch (GalaxyException e) { throw new McpException(e.Message); }
            catch (System.Runtime.InteropServices.COMException e)
            {
                throw new McpException("AVEVA COM error 0x" + e.ErrorCode.ToString("X8") + ": " + e.Message);
            }
        }

        public static string Run(Action action, string done) => Run(() => { action(); return done; });
    }

    [McpServerToolType]
    public class GalaxyReadTools(GalaxySession session)
    {
        [McpServerTool(Name = "galaxy_status", ReadOnly = true)]
        [Description("Show which Galaxy the server is connected to, which backend it uses, and which kinds of change (configure, delete, deploy, import, runtime-write) it is allowed to make. Call this first.")]
        public ConnectionInfo Status() => GalaxySession.Run(session.Status);

        [McpServerTool(Name = "galaxy_list", ReadOnly = true)]
        [Description("List the Galaxies on a Galaxy Repository node.")]
        public IList<string> List([Description("GR node name. Defaults to the configured node.")] string node = null)
            => GalaxySession.Run(() => session.Backend.ListGalaxies(node ?? session.Options.Node));

        [McpServerTool(Name = "galaxy_connect")]
        [Description("Log in to a Galaxy. Not needed when the server was started with AVEVA_SP_GALAXY set. Leave user and password empty to use the configured credentials (or no Galaxy security).")]
        public ConnectionInfo Connect(
            [Description("Galaxy name, as returned by galaxy_list.")] string galaxy,
            [Description("GR node name. Defaults to the configured node.")] string node = null,
            string user = null,
            string password = null)
            => GalaxySession.Run(() =>
            {
                if (session.Backend.IsConnected) session.Backend.Disconnect();
                session.Backend.Connect(node ?? session.Options.Node, galaxy, user ?? session.Options.User, password ?? session.Options.Password);
                return session.Status();
            });

        [McpServerTool(Name = "object_query", ReadOnly = true)]
        [Description("Find templates and instances in the Galaxy. All filters are optional and combine with AND. Returns at most 'max' objects, sorted by tagname.")]
        public IList<GalaxyObjectInfo> Query(
            [Description("'template', 'instance' or 'any'.")] string kind = "any",
            [Description("Tagname pattern, % or * as wildcard, e.g. 'Pump_%' or '$*Valve*'.")] string nameLike = null,
            [Description("Only objects derived (directly or indirectly) from this template, e.g. '$Pump'.")] string derivedFrom = null,
            [Description("Only objects contained by this object.")] string containedBy = null,
            [Description("Only instances in this area.")] string area = null,
            [Description("Only instances hosted by this engine/platform.")] string host = null,
            int max = 200)
            => GalaxySession.Run(() =>
            {
                kind = (kind ?? "any").ToLowerInvariant();
                if (kind != "template" && kind != "instance" && kind != "any")
                    throw new GalaxyException("kind must be 'template', 'instance' or 'any'.");
                return session.Galaxy().Query(new ObjectQuery
                {
                    Kind = kind, NameLike = nameLike, DerivedFrom = derivedFrom, ContainedBy = containedBy,
                    Area = area, Host = host, Max = Math.Max(1, Math.Min(max, 5000)),
                });
            });

        [McpServerTool(Name = "object_get", ReadOnly = true)]
        [Description("Get one object's identity and state: parent template, containment, area, host, check-out status, version and deployment status.")]
        public GalaxyObjectInfo Get([Description("Tagname, e.g. 'Pump_001' or '$Pump'.")] string tagname)
            => GalaxySession.Run(() => session.Galaxy().GetObject(tagname));

        [McpServerTool(Name = "object_attributes", ReadOnly = true)]
        [Description("List an object's attributes with data type, configured value, category, security classification and lock state. Objects can have hundreds of attributes: pass nameContains to narrow it down.")]
        public IList<AttributeInfo> Attributes(
            string tagname,
            [Description("Only attributes whose name contains this text (case-insensitive).")] string nameContains = null,
            [Description("true = only attributes that can be configured in the IDE; false = everything, including run-time and script attributes.")] bool configurableOnly = false,
            int max = 300)
            => GalaxySession.Run(() =>
                (IList<AttributeInfo>)session.Galaxy().GetAttributes(tagname, nameContains, configurableOnly).Take(Math.Max(1, max)).ToList());

        [McpServerTool(Name = "object_scripts", ReadOnly = true)]
        [Description("List the scripts on an object with their trigger and full script text (declarations, startup, on-scan, execute, off-scan, shutdown).")]
        public IList<Dictionary<string, object>> Scripts(
            string tagname,
            [Description("Only this script. Omit for all scripts.")] string scriptName = null)
            => GalaxySession.Run(() => ScriptReader.Read(session.Galaxy().GetAttributes(tagname, null, false), scriptName));
    }

    /// <summary>Scripts are not separate entities in a Galaxy: each is a group of "Name.Part" attributes.</summary>
    public static class ScriptReader
    {
        public static readonly string[] Parts =
        {
            "Expression", "TriggerType", "TriggerPeriod", "DeclarationsText", "StartupText",
            "OnScanText", "ExecuteText", "OffScanText", "ShutdownText",
        };

        public static IList<Dictionary<string, object>> Read(IList<AttributeInfo> attributes, string onlyScript)
        {
            const string marker = ".ExecuteText";
            var byName = attributes.ToDictionary(a => a.Name, StringComparer.OrdinalIgnoreCase);
            var scripts = new List<Dictionary<string, object>>();
            foreach (var exec in attributes.Where(a => a.Name.EndsWith(marker, StringComparison.OrdinalIgnoreCase)))
            {
                var name = exec.Name.Substring(0, exec.Name.Length - marker.Length);
                if (onlyScript != null && !name.Equals(onlyScript, StringComparison.OrdinalIgnoreCase)) continue;
                var script = new Dictionary<string, object> { ["Name"] = name };
                foreach (var part in Parts)
                    if (byName.TryGetValue(name + "." + part, out var a) && !IsEmpty(a.Value)) script[part] = a.Value;
                scripts.Add(script);
            }
            if (onlyScript != null && scripts.Count == 0) throw new GalaxyException("No script named '" + onlyScript + "' on this object.");
            return scripts;
        }

        private static bool IsEmpty(object v) => v == null || (v is string s && s.Length == 0);
    }

    [McpServerToolType]
    public class GalaxyWriteTools(GalaxySession session)
    {
        private IGalaxyBackend Configure() { session.Policy.Require(Permission.Configure); return session.Galaxy(); }

        [McpServerTool(Name = "template_derive")]
        [Description("Create a new template derived from an existing one. Requires the 'configure' permission.")]
        public GalaxyObjectInfo DeriveTemplate(
            [Description("Parent template, e.g. '$UserDefined'.")] string parentTemplate,
            [Description("New template name, starting with '$'.")] string newName)
            => GalaxySession.Run(() => Configure().DeriveTemplate(parentTemplate, newName));

        [McpServerTool(Name = "instance_create")]
        [Description("Create a new instance of a template. The instance is not deployed; assign it with object_assign and deploy it with objects_deploy. Requires the 'configure' permission.")]
        public GalaxyObjectInfo CreateInstance(
            [Description("Template to instantiate, e.g. '$Pump'.")] string template,
            [Description("New instance tagname.")] string newName)
            => GalaxySession.Run(() => Configure().CreateInstance(template, newName));

        [McpServerTool(Name = "object_checkout")]
        [Description("Check an object out, to hold it while making several edits by hand in the IDE. attributes_set, uda_add, script_set and object_assign check out and in by themselves; do not check out before calling them. Requires 'configure'.")]
        public string CheckOut(string tagname)
            => GalaxySession.Run(() => Configure().CheckOut(tagname), "Checked out " + tagname);

        [McpServerTool(Name = "object_checkin")]
        [Description("Check in an object that is checked out to this user. Requires 'configure'.")]
        public string CheckIn(string tagname, [Description("Change comment stored in the object's history.")] string comment)
            => GalaxySession.Run(() => Configure().CheckIn(tagname, comment ?? ""), "Checked in " + tagname);

        [McpServerTool(Name = "object_undo_checkout")]
        [Description("Discard the changes of a checked-out object and release it. Requires 'configure'.")]
        public string UndoCheckOut(string tagname)
            => GalaxySession.Run(() => Configure().UndoCheckOut(tagname), "Check-out undone for " + tagname);

        [McpServerTool(Name = "attributes_set")]
        [Description("Set the configured value of one or more attributes of an object, as one check-out / save / check-in. If anything fails the check-out is undone and nothing changes. Changes to a template propagate to derived objects unless they override the attribute. Deployed instances are left with pending changes until redeployed. Requires 'configure'.")]
        public string SetAttributes(
            string tagname,
            [Description("Attribute name -> new value as text, e.g. {\"SpeedSP\": \"42.5\", \"ShortDesc\": \"Raw water pump\", \"Running\": \"true\"}.")] Dictionary<string, string> values,
            [Description("Check-in comment.")] string comment)
            => GalaxySession.Run(() =>
            {
                if (values == null || values.Count == 0) throw new GalaxyException("values is empty.");
                Configure().SetAttributes(tagname, values, comment ?? "");
                return "Set " + values.Count + " attribute(s) on " + tagname;
            });

        [McpServerTool(Name = "uda_add")]
        [Description("Add a user-defined attribute (UDA) to a template or instance. Requires 'configure'.")]
        public string AddUda(
            string tagname,
            [Description("Name of the new attribute.")] string name,
            [Description("MxBoolean, MxInteger, MxFloat, MxDouble, MxString, MxTime or MxElapsedTime.")] string dataType,
            [Description("Check-in comment.")] string comment,
            [Description("Initial value as text. Omit for the type's default.")] string initialValue = null,
            [Description("Attribute category. Default lets users, supervisory code and config write it.")] string category = "MxCategoryWriteable_USC_Lockable",
            [Description("Security classification, e.g. MxSecurityFreeAccess, MxSecurityOperate, MxSecurityTune, MxSecurityConfigure.")] string security = "MxSecurityOperate",
            [Description("Array length; 0 for a scalar.")] int arrayLength = 0)
            => GalaxySession.Run(() =>
            {
                Configure().AddUda(tagname, new UdaSpec
                {
                    Name = name, DataType = dataType, Category = category, Security = security,
                    IsArray = arrayLength > 0, ArrayLength = Math.Max(0, arrayLength), InitialValue = initialValue,
                }, comment ?? "");
                return "Added " + tagname + "." + name;
            });

        [McpServerTool(Name = "script_set")]
        [Description("Create a script on an object, or replace parts of an existing one. Only the parts you pass are changed. Script text is AVEVA QuickScript .NET. Read the current script with object_scripts first when editing. Requires 'configure'.")]
        public string SetScript(
            string tagname,
            [Description("Script name, e.g. 'RunHoursCalc'.")] string scriptName,
            [Description("Check-in comment.")] string comment,
            [Description("Body that runs when the trigger fires.")] string executeText = null,
            [Description("Trigger expression, e.g. 'Me.Running'.")] string expression = null,
            [Description("WhileTrue, WhileFalse, OnTrue, OnFalse, DataChange or Periodic.")] string triggerType = null,
            [Description("Variable declarations (Dim ...).")] string declarationsText = null,
            string startupText = null,
            string onScanText = null,
            string offScanText = null,
            string shutdownText = null)
            => GalaxySession.Run(() =>
            {
                var spec = new ScriptSpec
                {
                    Name = scriptName, ExecuteText = executeText, Expression = expression, TriggerType = triggerType,
                    DeclarationsText = declarationsText, StartupText = startupText, OnScanText = onScanText,
                    OffScanText = offScanText, ShutdownText = shutdownText,
                };
                if (string.IsNullOrWhiteSpace(scriptName)) throw new GalaxyException("scriptName is required.");
                if (spec.ToAttributes().Count == 0) throw new GalaxyException("Pass at least one script part to set.");
                Configure().SetScript(tagname, spec, comment ?? "");
                return "Script " + tagname + "." + scriptName + " saved";
            });

        [McpServerTool(Name = "object_assign")]
        [Description("Assign an instance to an area, a container and/or a host engine. Omit a parameter to leave it unchanged; pass an empty string to unassign. Requires 'configure'.")]
        public GalaxyObjectInfo Assign(
            string tagname,
            [Description("Check-in comment.")] string comment,
            string area = null,
            string container = null,
            string host = null)
            => GalaxySession.Run(() =>
            {
                if (area == null && container == null && host == null) throw new GalaxyException("Pass area, container or host.");
                var g = Configure();
                g.Assign(tagname, area, container, host, comment ?? "");
                return g.GetObject(tagname);
            });

        [McpServerTool(Name = "object_delete", Destructive = true)]
        [Description("Permanently delete a template or instance from the Galaxy. Fails for deployed instances and for templates that still have derived objects. This cannot be undone: confirm with the user before calling. Requires the 'delete' permission.")]
        public string Delete(string tagname)
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.Delete);
                session.Galaxy().Delete(tagname);
                return "Deleted " + tagname;
            });

        [McpServerTool(Name = "objects_deploy", Destructive = true)]
        [Description("Deploy instances to the run-time (deploys pending changes for instances that are already deployed). This changes the running plant system: confirm with the user before calling. Hosts must be deployed before the objects they host. Returns one result per instance. Requires the 'deploy' permission.")]
        public IList<OperationResult> Deploy([Description("Instance tagnames, in the order to deploy them.")] string[] tagnames)
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.Deploy);
                return session.Galaxy().Deploy(RequireNames(tagnames));
            });

        [McpServerTool(Name = "objects_undeploy", Destructive = true)]
        [Description("Undeploy instances: they stop running and their run-time data is no longer available. Confirm with the user before calling. Returns one result per instance. Requires the 'deploy' permission.")]
        public IList<OperationResult> Undeploy(string[] tagnames)
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.Deploy);
                return session.Galaxy().Undeploy(RequireNames(tagnames));
            });

        internal static IList<string> RequireNames(string[] tagnames)
        {
            var names = (tagnames ?? new string[0]).Where(t => !string.IsNullOrWhiteSpace(t)).Select(t => t.Trim()).ToList();
            if (names.Count == 0) throw new GalaxyException("tagnames is empty.");
            return names;
        }
    }

    [McpServerToolType]
    public class RuntimeTools(GalaxySession session)
    {
        [McpServerTool(Name = "runtime_read", ReadOnly = true)]
        [Description("Read live values of attributes on deployed objects, with quality and timestamp. Use it to check that a deployed change behaves as intended.")]
        public IList<RuntimeValue> Read(
            [Description("Attribute references, e.g. ['Pump_001.Running', 'Pump_001.SpeedSP'].")] string[] references,
            [Description("How long to wait for values, in milliseconds.")] int timeoutMs = 5000)
            => GalaxySession.Run(() => session.Runtime.Read(GalaxyWriteTools.RequireNames(references), Clamp(timeoutMs)));

        [McpServerTool(Name = "runtime_write", Destructive = true)]
        [Description("Write a live value to an attribute of a deployed object. This acts on the running plant and may start or stop equipment: confirm with the user before calling. Requires the 'runtime-write' permission.")]
        public OperationResult Write(
            [Description("Attribute reference, e.g. 'Pump_001.SpeedSP'.")] string reference,
            [Description("New value as text; converted to the attribute's data type.")] string value,
            int timeoutMs = 5000)
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.RuntimeWrite);
                if (string.IsNullOrWhiteSpace(reference)) throw new GalaxyException("reference is required.");
                return session.Runtime.Write(reference.Trim(), value, Clamp(timeoutMs));
            });

        private static int Clamp(int timeoutMs) => Math.Max(500, Math.Min(timeoutMs, 60000));
    }

    [McpServerToolType]
    public class TransferTools(GalaxySession session)
    {
        [McpServerTool(Name = "objects_export")]
        [Description("Export objects to a file in the server's transfer directory: 'aaPKG' (full objects, re-importable, good for backups before a change) or 'csv' (galaxy dump of attributes, good for bulk edits and diffs). Returns the full path of the file. Does not change the Galaxy.")]
        public string Export(
            [Description("Templates and/or instances to export.")] string[] tagnames,
            [Description("'aaPKG' or 'csv'.")] string format,
            [Description("File name inside the transfer directory, e.g. 'pumps-before-change.aaPKG'.")] string fileName)
            => GalaxySession.Run(() =>
            {
                var f = (format ?? "").ToLowerInvariant();
                if (f != "aapkg" && f != "csv") throw new GalaxyException("format must be 'aaPKG' or 'csv'.");
                var path = session.Policy.ResolveTransferPath(fileName);
                return session.Galaxy().Export(GalaxyWriteTools.RequireNames(tagnames), f == "csv" ? "csv" : "aaPKG", path);
            });

        [McpServerTool(Name = "package_import", Destructive = true)]
        [Description("Import an .aaPKG file from the transfer directory into the Galaxy. With overwrite=true existing objects with the same name are replaced: confirm with the user first. Requires the 'import' permission.")]
        public string ImportPackage(
            [Description("File name inside the transfer directory.")] string fileName,
            [Description("Replace objects that already exist.")] bool overwrite = false)
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.Import);
                var path = RequireExisting(fileName);
                session.Galaxy().ImportPackage(path, overwrite);
                return "Imported " + path;
            });

        [McpServerTool(Name = "csv_load", Destructive = true)]
        [Description("Load a galaxy dump CSV from the transfer directory (the format objects_export writes with format 'csv'), creating and updating instances in bulk. Confirm with the user first. Requires the 'import' permission.")]
        public string LoadCsv(
            [Description("File name inside the transfer directory.")] string fileName,
            [Description("'update' = create missing and update existing instances; 'skip' = create missing, leave existing untouched.")] string mode = "skip")
            => GalaxySession.Run(() =>
            {
                session.Policy.Require(Permission.Import);
                var m = (mode ?? "").ToLowerInvariant();
                if (m != "update" && m != "skip") throw new GalaxyException("mode must be 'update' or 'skip'.");
                var path = RequireExisting(fileName);
                session.Galaxy().LoadCsv(path, m);
                return "Loaded " + path;
            });

        private string RequireExisting(string fileName)
        {
            var path = session.Policy.ResolveTransferPath(fileName);
            if (!File.Exists(path)) throw new GalaxyException("File not found: " + path);
            return path;
        }
    }
}
