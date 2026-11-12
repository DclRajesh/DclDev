using System;
using System.Threading.Tasks;
using AvevaSpMcp.Backends;
using AvevaSpMcp.Backends.Mock;
using AvevaSpMcp.Tools;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace AvevaSpMcp
{
    public static class Program
    {
        public static async Task<int> Main(string[] args)
        {
            ServerOptions options;
            IGalaxyBackend galaxy;
            IRuntimeBackend runtime;
            try
            {
                options = ServerOptions.FromEnvironment();
                galaxy = BackendFactory.CreateGalaxy(options);
                runtime = BackendFactory.CreateRuntime(options, galaxy);
            }
            catch (Exception e) when (e is ArgumentException || e is GalaxyException)
            {
                Console.Error.WriteLine("aveva-sp-mcp: " + e.Message);
                return 2;
            }

            var builder = Host.CreateApplicationBuilder(args);
            // stdout carries the MCP protocol; everything else must go to stderr.
            builder.Logging.ClearProviders();
            builder.Logging.AddConsole(o => o.LogToStandardErrorThreshold = LogLevel.Trace);

            var policy = new AccessPolicy(options);
            builder.Services.AddSingleton(options);
            builder.Services.AddSingleton(policy);
            builder.Services.AddSingleton(new GalaxySession(galaxy, runtime, options, policy));
            builder.Services
                .AddMcpServer(o =>
                {
                    o.ServerInfo = new ModelContextProtocol.Protocol.Implementation { Name = "aveva-sp-mcp", Version = "0.1.0" };
                    o.ServerInstructions =
                        "Tools for developing an AVEVA System Platform Galaxy. Start with galaxy_status. " +
                        "Reading is always allowed; each kind of change needs a permission the server operator grants. " +
                        "Before a change, read the current state; before deploy, delete, import or a run-time write, confirm with the user. " +
                        "Export objects to .aaPKG before risky edits so they can be restored.";
                })
                .WithStdioServerTransport()
                .WithTools<GalaxyReadTools>()
                .WithTools<GalaxyWriteTools>()
                .WithTools<RuntimeTools>()
                .WithTools<TransferTools>();

            Console.Error.WriteLine("aveva-sp-mcp: galaxy backend = " + galaxy.Name + ", runtime backend = " + runtime.Name +
                ", permissions = [" + string.Join(", ", policy.Granted()) + "]");
            await builder.Build().RunAsync();
            return 0;
        }
    }

    public static class BackendFactory
    {
        public static bool GRAccessAvailable =>
#if GRACCESS
            true;
#else
            false;
#endif

        public static bool MxAccessAvailable =>
#if MXACCESS
            true;
#else
            false;
#endif

        public static IGalaxyBackend CreateGalaxy(ServerOptions options)
        {
            switch (options.Backend)
            {
                case "mock":
                    return new MockGalaxyBackend();
                case "auto":
                case "graccess":
#if GRACCESS
                    return new AvevaSpMcp.Backends.Aveva.GRAccessGalaxyBackend();
#else
                    if (options.Backend == "auto")
                    {
                        Console.Error.WriteLine("aveva-sp-mcp: GRAccess is not compiled into this build; using the mock Galaxy. See README, 'Building on the System Platform machine'.");
                        if (options.Galaxy == null) options.Galaxy = MockGalaxyBackend.GalaxyName;
                        return new MockGalaxyBackend();
                    }
                    throw new GalaxyException("AVEVA_SP_BACKEND=graccess, but this build has no GRAccess backend. Build the net48 target on a machine with System Platform installed.");
#endif
                default:
                    throw new ArgumentException("AVEVA_SP_BACKEND must be 'auto', 'graccess' or 'mock' (was '" + options.Backend + "').");
            }
        }

        public static IRuntimeBackend CreateRuntime(ServerOptions options, IGalaxyBackend galaxy)
        {
            if (galaxy is MockGalaxyBackend) return new MockRuntimeBackend(galaxy);
#if MXACCESS
            return new AvevaSpMcp.Backends.Aveva.MxAccessRuntimeBackend();
#else
            return new UnavailableRuntimeBackend();
#endif
        }
    }

    /// <summary>Used when the Galaxy backend is real but MXAccess was not found at build time.</summary>
    public sealed class UnavailableRuntimeBackend : IRuntimeBackend
    {
        private const string Why = "Run-time access is not available: ArchestrA.MxAccess.dll was not found when this server was built.";
        public string Name => "none";
        public System.Collections.Generic.IList<RuntimeValue> Read(System.Collections.Generic.IList<string> references, int timeoutMs) => throw new GalaxyException(Why);
        public OperationResult Write(string reference, string value, int timeoutMs) => throw new GalaxyException(Why);
    }
}
