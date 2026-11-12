using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AvevaSpMcp;
using AvevaSpMcp.Backends.Mock;
using AvevaSpMcp.Tools;
using ModelContextProtocol;
using Xunit;

public class ToolTests
{
    private static GalaxySession Session(string allow = null, string transferDir = null)
    {
        var env = new Dictionary<string, string>
        {
            ["AVEVA_SP_BACKEND"] = "mock",
            ["AVEVA_SP_GALAXY"] = MockGalaxyBackend.GalaxyName,
            ["AVEVA_SP_ALLOW"] = allow,
            ["AVEVA_SP_TRANSFER_DIR"] = transferDir,
        };
        var options = ServerOptions.FromEnvironment(k => env.TryGetValue(k, out var v) ? v : null);
        var galaxy = new MockGalaxyBackend();
        return new GalaxySession(galaxy, new MockRuntimeBackend(galaxy), options, new AccessPolicy(options));
    }

    [Fact]
    public void Starts_read_only_and_connects_on_first_use()
    {
        var s = Session();
        Assert.Empty(s.Status().Permissions);
        Assert.False(s.Status().Connected);

        var pumps = new GalaxyReadTools(s).Query(kind: "instance", derivedFrom: "$Pump");

        Assert.Equal(new[] { "Pump_001", "Pump_002" }, pumps.Select(p => p.Tagname));
        Assert.True(s.Status().Connected);
    }

    [Fact]
    public void Query_matches_wildcards_and_indirect_derivation()
    {
        var read = new GalaxyReadTools(Session());
        Assert.Equal(new[] { "$Pump", "Pump_001", "Pump_002" }, read.Query(nameLike: "*pump_*").Concat(read.Query(nameLike: "$P%")).Select(o => o.Tagname).OrderBy(n => n, StringComparer.Ordinal));
        Assert.Contains(read.Query(derivedFrom: "$UserDefined"), o => o.Tagname == "Pump_001");
    }

    [Theory]
    [InlineData("configure", "deploy")]
    [InlineData("deploy", "configure")]
    public void A_change_needs_its_own_permission(string granted, string needed)
    {
        var write = new GalaxyWriteTools(Session(allow: granted));
        Action act = needed == "deploy"
            ? () => write.Deploy(new[] { "Pump_002" })
            : () => write.SetAttributes("Pump_002", new Dictionary<string, string> { ["SpeedSP"] = "1" }, "x");

        var e = Assert.Throws<McpException>(act);
        Assert.Contains("'" + needed + "'", e.Message);
        Assert.Contains("AVEVA_SP_ALLOW", e.Message);
    }

    [Fact]
    public void Unknown_permission_name_is_rejected_at_startup()
    {
        Assert.Throws<ArgumentException>(() => Session(allow: "configure,depoly"));
        Assert.Equal(5, ServerOptions.ParsePermissions("all").Distinct().Count());
        Assert.Equal(Permission.RuntimeWrite, ServerOptions.ParsePermissions("runtime-write").Single());
    }

    [Fact]
    public void Set_attributes_converts_types_bumps_version_and_marks_pending_changes()
    {
        var s = Session(allow: "configure");
        new GalaxyWriteTools(s).SetAttributes("Pump_001",
            new Dictionary<string, string> { ["SpeedSP"] = "42.5", ["Running"] = "true", ["ShortDesc"] = "Raw water" }, "tune");

        var read = new GalaxyReadTools(s);
        var attrs = read.Attributes("Pump_001").ToDictionary(a => a.Name, a => a.Value);
        Assert.Equal(42.5f, attrs["SpeedSP"]);
        Assert.Equal(true, attrs["Running"]);
        Assert.Equal("Raw water", attrs["ShortDesc"]);
        var info = read.Get("Pump_001");
        Assert.Equal("2", info.ConfigVersion);
        Assert.Equal("notCheckedOut", info.CheckoutStatus);
        Assert.Equal("DeployedWithPendingChanges", info.DeploymentStatus);
    }

    [Fact]
    public void A_failed_edit_changes_nothing_and_leaves_no_checkout()
    {
        var s = Session(allow: "configure");
        var write = new GalaxyWriteTools(s);

        var e = Assert.Throws<McpException>(() => write.SetAttributes("Pump_001",
            new Dictionary<string, string> { ["ShortDesc"] = "changed", ["SpeedSP"] = "fast" }, "x"));

        Assert.Contains("not a valid MxFloat", e.Message);
        var read = new GalaxyReadTools(s);
        Assert.Equal("", read.Attributes("Pump_001", "ShortDesc").Single().Value);
        Assert.Equal("notCheckedOut", read.Get("Pump_001").CheckoutStatus);
        Assert.Throws<McpException>(() => write.SetAttributes("Pump_001", new Dictionary<string, string> { ["Nope"] = "1" }, "x"));
    }

    [Fact]
    public void Scripts_are_read_and_edited_as_attribute_groups()
    {
        var s = Session(allow: "configure");
        var read = new GalaxyReadTools(s);
        var write = new GalaxyWriteTools(s);

        var inherited = read.Scripts("Pump_001").Single();
        Assert.Equal("RunHoursCalc", inherited["Name"]);
        Assert.Equal("Me.Running", inherited["Expression"]);

        write.SetScript("$Pump", "Trip", "add trip", executeText: "Me.Running = false;", expression: "Me.SpeedSP > 100", triggerType: "OnTrue");
        write.SetScript("$Pump", "Trip", "retune", expression: "Me.SpeedSP > 90");

        var trip = read.Scripts("$Pump", "Trip").Single();
        Assert.Equal("Me.Running = false;", trip["ExecuteText"]);
        Assert.Equal("Me.SpeedSP > 90", trip["Expression"]);
        Assert.Equal("OnTrue", trip["TriggerType"]);
        Assert.Throws<McpException>(() => read.Scripts("$Pump", "Missing"));
    }

    [Fact]
    public void New_instance_inherits_attributes_and_can_be_assigned_and_deployed()
    {
        var s = Session(allow: "configure,deploy");
        var write = new GalaxyWriteTools(s);
        write.AddUda("$Pump", "Fault", "MxBoolean", "add fault");

        var created = write.CreateInstance("$Pump", "Pump_003");
        Assert.Equal("Undeployed", created.DeploymentStatus);
        Assert.Contains(new GalaxyReadTools(s).Attributes("Pump_003"), a => a.Name == "SpeedSP");
        Assert.Throws<McpException>(() => write.CreateInstance("$Pump", "Pump_003"));

        var assigned = write.Assign("Pump_003", "place", area: "Area_PumpStation", host: "AppEngine_001");
        Assert.Equal("Area_PumpStation", assigned.Area);

        var results = write.Deploy(new[] { "Pump_003", "$Pump", "Ghost" });
        Assert.Equal(new[] { true, false, false }, results.Select(r => r.Success));
    }

    [Fact]
    public void Deployed_instances_cannot_be_deleted()
    {
        var write = new GalaxyWriteTools(Session(allow: "delete"));
        Assert.Contains("Undeploy", Assert.Throws<McpException>(() => write.Delete("Pump_001")).Message);
        Assert.Equal("Deleted Pump_002", write.Delete("Pump_002"));
    }

    [Fact]
    public void Runtime_reads_deployed_objects_and_writes_only_with_permission()
    {
        var s = Session(allow: "runtime-write");
        var rt = new RuntimeTools(s);

        var values = rt.Read(new[] { "Pump_001.SpeedSP", "Pump_002.SpeedSP", "Pump_001.Nope" });
        Assert.Equal(new[] { "Good", "Bad", "Bad" }, values.Select(v => v.Quality));

        Assert.True(rt.Write("Pump_001.SpeedSP", "12.5").Success);
        Assert.Equal(12.5f, rt.Read(new[] { "Pump_001.SpeedSP" }).Single().Value);
        Assert.Throws<McpException>(() => new RuntimeTools(Session()).Write("Pump_001.SpeedSP", "1"));
    }

    [Fact]
    public void Export_stays_inside_the_transfer_directory()
    {
        var dir = Path.Combine(Path.GetTempPath(), "aveva-sp-mcp-tests-" + Guid.NewGuid().ToString("N"));
        try
        {
            var transfer = new TransferTools(Session(transferDir: dir));

            var path = transfer.Export(new[] { "$Pump", "Pump_001" }, "csv", "pumps.csv");

            Assert.Equal(Path.Combine(dir, "pumps.csv"), path);
            Assert.Contains("Pump_001", File.ReadAllText(path));
            Assert.Throws<McpException>(() => transfer.Export(new[] { "$Pump" }, "csv", Path.Combine("..", "escape.csv")));
            Assert.Throws<McpException>(() => transfer.Export(new[] { "$Pump" }, "csv", Path.Combine(Path.GetTempPath(), "abs.csv")));
            Assert.Throws<McpException>(() => transfer.Export(new[] { "$Pump" }, "zip", "x.zip"));
            Assert.Contains("'import'", Assert.Throws<McpException>(() => transfer.ImportPackage("pumps.csv")).Message);
        }
        finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
    }
}
