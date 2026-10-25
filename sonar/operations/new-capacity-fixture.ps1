param(
    [Parameter(Mandatory)][string]$Destination,
    [Parameter(Mandatory)][ValidateSet(250, 1000)][int]$Classes
)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Destination) { throw 'The synthetic fixture destination must be new.' }
New-Item -ItemType Directory -Path (Join-Path $Destination '.config') -Force | Out-Null
'{"sdk":{"version":"10.0.402","rollForward":"disable"}}' | Set-Content (Join-Path $Destination 'global.json') -Encoding utf8NoBOM
'{"version":1,"isRoot":true,"tools":{"dotnet-sonarscanner":{"version":"12.0.0","commands":["dotnet-sonarscanner"],"rollForward":false}}}' | Set-Content (Join-Path $Destination '.config/dotnet-tools.json') -Encoding utf8NoBOM
'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework><IsPackable>false</IsPackable><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>' | Set-Content (Join-Path $Destination 'CapacityFixture.csproj') -Encoding utf8NoBOM

for ($class = 0; $class -lt $Classes; $class++) {
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add('namespace CapacityFixture;')
    $lines.Add("public static class Calculator$class")
    $lines.Add('{')
    for ($method = 0; $method -lt 4; $method++) {
        $lines.Add("    public static int Calculate$method(int input)")
        $lines.Add('    {')
        $lines.Add("        var result = input + $class;")
        for ($condition = 0; $condition -lt 4; $condition++) {
            $divisor = 2 + $method + $condition
            $lines.Add("        if (input % $divisor == $condition)")
            $lines.Add('        {')
            $lines.Add("            result += $($class + $method + $condition + 1);")
            $lines.Add('        }')
        }
        $lines.Add('        return result;')
        $lines.Add('    }')
    }
    $lines.Add('}')
    [IO.File]::WriteAllLines((Join-Path $Destination "Calculator$class.cs"), $lines, [Text.UTF8Encoding]::new($false))
}

[ordered]@{ classes = $Classes; methods = $Classes * 4; sourceFiles = $Classes; purpose = 'Synthetic C# capacity fixture only; no package, readiness or representative application-quality claim.' } |
    ConvertTo-Json | Set-Content (Join-Path $Destination 'fixture.json') -Encoding utf8NoBOM
