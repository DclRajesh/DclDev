using System;
using System.Collections.Generic;

namespace AvevaSpMcp
{
    /// <summary>Error that is safe and useful to show to the MCP client.</summary>
    public class GalaxyException : Exception
    {
        public GalaxyException(string message) : base(message) { }
        public GalaxyException(string message, Exception inner) : base(message, inner) { }
    }

    public class ConnectionInfo
    {
        public bool Connected { get; set; }
        public string Backend { get; set; }
        public string Node { get; set; }
        public string Galaxy { get; set; }
        public string User { get; set; }
        public string[] Permissions { get; set; }
        public string TransferDirectory { get; set; }
    }

    public class GalaxyObjectInfo
    {
        public string Tagname { get; set; }
        /// <summary>"template" or "instance".</summary>
        public string Kind { get; set; }
        public string DerivedFrom { get; set; }
        public string BasedOn { get; set; }
        public string ContainedName { get; set; }
        public string HierarchicalName { get; set; }
        public string Container { get; set; }
        public string Area { get; set; }
        public string Host { get; set; }
        public string CheckoutStatus { get; set; }
        public string CheckedOutBy { get; set; }
        public string ConfigVersion { get; set; }
        public string DeploymentStatus { get; set; }
    }

    public class AttributeInfo
    {
        public string Name { get; set; }
        public string DataType { get; set; }
        public object Value { get; set; }
        public string Category { get; set; }
        public string Security { get; set; }
        public string Locked { get; set; }
        public string Description { get; set; }
    }

    public class ObjectQuery
    {
        /// <summary>"template", "instance" or "any".</summary>
        public string Kind { get; set; } = "any";
        /// <summary>Tagname pattern; % or * is a wildcard.</summary>
        public string NameLike { get; set; }
        public string DerivedFrom { get; set; }
        public string ContainedBy { get; set; }
        public string Area { get; set; }
        public string Host { get; set; }
        public int Max { get; set; } = 200;
    }

    public class UdaSpec
    {
        public string Name { get; set; }
        /// <summary>MxBoolean, MxInteger, MxFloat, MxDouble, MxString, MxTime, MxElapsedTime ...</summary>
        public string DataType { get; set; } = "MxFloat";
        public string Category { get; set; } = "MxCategoryWriteable_USC_Lockable";
        public string Security { get; set; } = "MxSecurityOperate";
        public bool IsArray { get; set; }
        public int ArrayLength { get; set; }
        public string InitialValue { get; set; }
    }

    public class ScriptSpec
    {
        public string Name { get; set; }
        public string ExecuteText { get; set; }
        public string Expression { get; set; }
        /// <summary>WhileTrue, WhileFalse, OnTrue, OnFalse, DataChange, Periodic.</summary>
        public string TriggerType { get; set; }
        public string DeclarationsText { get; set; }
        public string StartupText { get; set; }
        public string OnScanText { get; set; }
        public string OffScanText { get; set; }
        public string ShutdownText { get; set; }

        /// <summary>The attribute name / value pairs this script maps to on the object.</summary>
        public IDictionary<string, string> ToAttributes()
        {
            var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            void Add(string suffix, string v) { if (v != null) d[Name + "." + suffix] = v; }
            Add("DeclarationsText", DeclarationsText);
            Add("ExecuteText", ExecuteText);
            Add("StartupText", StartupText);
            Add("OnScanText", OnScanText);
            Add("OffScanText", OffScanText);
            Add("ShutdownText", ShutdownText);
            Add("Expression", Expression);
            Add("TriggerType", TriggerType);
            return d;
        }
    }

    public class OperationResult
    {
        public string Target { get; set; }
        public bool Success { get; set; }
        public string Message { get; set; }
    }

    public class RuntimeValue
    {
        public string Reference { get; set; }
        public object Value { get; set; }
        public string Quality { get; set; }
        public string Timestamp { get; set; }
        public string Status { get; set; }
    }
}
