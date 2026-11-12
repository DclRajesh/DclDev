using System.Collections.Generic;

namespace AvevaSpMcp.Backends
{
    /// <summary>
    /// Configuration-time access to a Galaxy (what the IDE does). Implementations do no permission
    /// checks: those live in the tool layer (see <see cref="AccessPolicy"/>).
    /// </summary>
    public interface IGalaxyBackend
    {
        string Name { get; }

        IList<string> ListGalaxies(string node);
        void Connect(string node, string galaxy, string user, string password);
        void Disconnect();
        bool IsConnected { get; }
        string Node { get; }
        string Galaxy { get; }
        string User { get; }

        IList<GalaxyObjectInfo> Query(ObjectQuery query);
        GalaxyObjectInfo GetObject(string tagname);
        IList<AttributeInfo> GetAttributes(string tagname, string nameContains, bool configurableOnly);

        GalaxyObjectInfo DeriveTemplate(string parentTemplate, string newName);
        GalaxyObjectInfo CreateInstance(string template, string newName);

        void CheckOut(string tagname);
        void CheckIn(string tagname, string comment);
        void UndoCheckOut(string tagname);

        /// <summary>Check out, set every value, save and check in. Undoes the check-out on failure.</summary>
        void SetAttributes(string tagname, IDictionary<string, string> values, string comment);
        void AddUda(string tagname, UdaSpec uda, string comment);
        /// <summary>Create the script if it does not exist, then set the parts given in the spec.</summary>
        void SetScript(string tagname, ScriptSpec script, string comment);
        /// <summary>Set area, container and/or host of an instance. Null leaves a relation unchanged.</summary>
        void Assign(string tagname, string area, string container, string host, string comment);
        void Delete(string tagname);

        IList<OperationResult> Deploy(IList<string> tagnames);
        IList<OperationResult> Undeploy(IList<string> tagnames);

        /// <summary>format: "aaPKG" or "csv". Returns the file written.</summary>
        string Export(IList<string> tagnames, string format, string path);
        void ImportPackage(string path, bool overwrite);
        void LoadCsv(string path, string mode);
    }

    /// <summary>Run-time access to deployed objects (what an HMI client does).</summary>
    public interface IRuntimeBackend
    {
        string Name { get; }
        IList<RuntimeValue> Read(IList<string> references, int timeoutMs);
        OperationResult Write(string reference, string value, int timeoutMs);
    }
}
