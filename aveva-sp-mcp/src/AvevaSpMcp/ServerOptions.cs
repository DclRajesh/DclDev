using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace AvevaSpMcp
{
    /// <summary>What a tool call is allowed to do. Reading is always allowed.</summary>
    public enum Permission
    {
        /// <summary>Create/derive objects, check in/out, edit attributes and scripts.</summary>
        Configure,
        /// <summary>Delete templates and instances.</summary>
        Delete,
        /// <summary>Deploy and undeploy instances.</summary>
        Deploy,
        /// <summary>Import .aaPKG files and load galaxy CSV dumps.</summary>
        Import,
        /// <summary>Write live attribute values on running objects.</summary>
        RuntimeWrite,
    }

    /// <summary>Settings, read once from environment variables at start-up.</summary>
    public class ServerOptions
    {
        public string Backend { get; set; }
        public string Node { get; set; }
        public string Galaxy { get; set; }
        public string User { get; set; }
        public string Password { get; set; }
        public string TransferDirectory { get; set; }
        public HashSet<Permission> Allowed { get; } = new HashSet<Permission>();

        public static ServerOptions FromEnvironment(Func<string, string> env = null)
        {
            env = env ?? Environment.GetEnvironmentVariable;
            var o = new ServerOptions
            {
                Backend = (env("AVEVA_SP_BACKEND") ?? "auto").Trim().ToLowerInvariant(),
                Node = NullIfEmpty(env("AVEVA_SP_NODE")) ?? Environment.MachineName,
                Galaxy = NullIfEmpty(env("AVEVA_SP_GALAXY")),
                User = env("AVEVA_SP_USER") ?? "",
                Password = env("AVEVA_SP_PASSWORD") ?? "",
                TransferDirectory = NullIfEmpty(env("AVEVA_SP_TRANSFER_DIR"))
                    ?? Path.Combine(Path.GetTempPath(), "aveva-sp-mcp"),
            };
            foreach (var p in ParsePermissions(env("AVEVA_SP_ALLOW"))) o.Allowed.Add(p);
            return o;
        }

        /// <summary>Parses "configure,deploy" style lists. "all" grants everything. Unknown names throw.</summary>
        public static IEnumerable<Permission> ParsePermissions(string list)
        {
            if (string.IsNullOrWhiteSpace(list)) yield break;
            foreach (var raw in list.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var name = raw.Replace("-", "").Replace("_", "");
                if (name.Equals("all", StringComparison.OrdinalIgnoreCase))
                {
                    foreach (Permission p in Enum.GetValues(typeof(Permission))) yield return p;
                    continue;
                }
                if (!Enum.TryParse(name, true, out Permission parsed) || !Enum.IsDefined(typeof(Permission), parsed))
                    throw new ArgumentException(
                        "AVEVA_SP_ALLOW: unknown permission '" + raw + "'. Valid: all, " +
                        string.Join(", ", AccessPolicy.Names(Enum.GetValues(typeof(Permission)).Cast<Permission>())));
                yield return parsed;
            }
        }

        private static string NullIfEmpty(string s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();
    }

    /// <summary>
    /// Gate for everything that changes the Galaxy or the running plant. The server starts read-only;
    /// each kind of change has to be switched on by whoever configures the server (AVEVA_SP_ALLOW),
    /// so the model cannot grant itself more access.
    /// </summary>
    public class AccessPolicy
    {
        private readonly ServerOptions _options;
        public AccessPolicy(ServerOptions options) { _options = options; }

        public bool IsAllowed(Permission p) => _options.Allowed.Contains(p);

        public void Require(Permission p)
        {
            if (IsAllowed(p)) return;
            throw new GalaxyException(
                "This server is not allowed to do that: permission '" + Name(p) + "' is off. " +
                "The person running the server can enable it by adding '" + Name(p) +
                "' to the AVEVA_SP_ALLOW environment variable and restarting it.");
        }

        public string[] Granted() => Names(_options.Allowed.OrderBy(p => p)).ToArray();

        public static string Name(Permission p) => p == Permission.RuntimeWrite ? "runtime-write" : p.ToString().ToLowerInvariant();
        public static IEnumerable<string> Names(IEnumerable<Permission> ps) => ps.Select(Name);

        /// <summary>
        /// Resolves a file name for export/import inside the transfer directory. Paths that would
        /// land outside it are refused, so tools cannot read or overwrite arbitrary files.
        /// </summary>
        public string ResolveTransferPath(string fileName)
        {
            if (string.IsNullOrWhiteSpace(fileName)) throw new GalaxyException("A file name is required.");
            var root = Path.GetFullPath(_options.TransferDirectory);
            var full = Path.GetFullPath(Path.Combine(root, fileName));
            var prefix = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                throw new GalaxyException("'" + fileName + "' is outside the transfer directory (" + root + "). Use a file name relative to it.");
            Directory.CreateDirectory(Path.GetDirectoryName(full));
            return full;
        }
    }
}
