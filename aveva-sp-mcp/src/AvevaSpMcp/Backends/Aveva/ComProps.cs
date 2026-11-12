using System;
using System.Linq;
using System.Reflection;

namespace AvevaSpMcp.Backends.Aveva
{
    /// <summary>
    /// Reads and writes properties of COM interop objects by name, case-insensitively. The AVEVA
    /// interop assemblies are not consistent about casing (Tagname, basedOn, value ...) and differ
    /// slightly between versions, so descriptive properties go through here: a property that does
    /// not exist reads as null instead of breaking the build.
    /// </summary>
    public static class ComProps
    {
        public static object Get(object target, Type interfaceType, string name)
        {
            if (target == null) return null;
            var property = Find(interfaceType, name);
            if (property == null || !property.CanRead) return null;
            try { return property.GetValue(target, null); }
            catch (TargetInvocationException) { return null; }
            catch (InvalidCastException) { return null; } // object does not implement the interface
        }

        public static string Text(object target, Type interfaceType, string name)
        {
            var value = Get(target, interfaceType, name);
            return value == null ? null : Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
        }

        public static void Set(object target, Type interfaceType, string name, object value)
        {
            var property = Find(interfaceType, name);
            if (property == null || !property.CanWrite)
                throw new GalaxyException(interfaceType.Name + " has no writable property '" + name + "' in this toolkit version.");
            try { property.SetValue(target, value, null); }
            catch (TargetInvocationException e) when (e.InnerException != null)
            {
                throw new GalaxyException("Setting " + name + " failed: " + e.InnerException.Message, e.InnerException);
            }
        }

        private static PropertyInfo Find(Type interfaceType, string name)
        {
            return new[] { interfaceType }.Concat(interfaceType.GetInterfaces())
                .SelectMany(t => t.GetProperties())
                .FirstOrDefault(p => string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase));
        }
    }
}
