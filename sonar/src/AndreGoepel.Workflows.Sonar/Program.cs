using System.Text.Json;
using AndreGoepel.Workflows.Sonar;

using var cancellation = new CancellationTokenSource(TimeSpan.FromMinutes(75));
Console.CancelKeyPress += (_, signal) =>
{
    signal.Cancel = true;
    cancellation.Cancel();
};

try
{
    if (args.Any(arg => arg.StartsWith("--nuget-", StringComparison.Ordinal)))
    {
        var publication = await NuGetCommand.RunAsync(args, cancellation.Token);
        if (publication.IsFailure)
            await Console.Error.WriteLineAsync(publication.Error);

        return publication.IsSuccess ? 0 : 1;
    }

    if (args is ["--root", var root, "--trusted-main"])
    {
        var main = await MainCommand.RunAsync(root, cancellation.Token);
        if (main.IsFailure)
            await Console.Error.WriteLineAsync(main.Error);

        return main.IsSuccess ? 0 : 1;
    }

    var parsed = AnalysisOptions.Parse(args);
    if (parsed.IsFailure)
    {
        await Console.Error.WriteLineAsync(parsed.Error);
        return 1;
    }

    var options = parsed.Value!;
    var scanToken = Environment.GetEnvironmentVariable("WORKFLOWS_SONAR_TOKEN") ?? "";
    var verifyToken = Environment.GetEnvironmentVariable("WORKFLOWS_SONAR_VERIFY_TOKEN") ?? "";
    Environment.SetEnvironmentVariable("WORKFLOWS_SONAR_TOKEN", null);
    Environment.SetEnvironmentVariable("WORKFLOWS_SONAR_VERIFY_TOKEN", null);

    using var client = SonarClient.CreateHttpClient();
    var contract = JsonSerializer.Deserialize<AnalysisContract>(
        await File.ReadAllTextAsync(
            Path.Combine(options.Root, "scripts/sonar/contract.json"),
            cancellation.Token
        ),
        JsonFormat.Options
    )!;

    var result = await new AnalysisRunner(
        new ProcessRunner(),
        new SonarClient(client, options.Url)
    ).RunAsync(options, contract, scanToken, verifyToken, cancellation.Token);
    if (result.IsFailure)
        await Console.Error.WriteLineAsync(result.Error);

    return result.IsSuccess ? 0 : 1;
}
catch (Exception error)
    when (error
            is IOException
                or InvalidDataException
                or UnauthorizedAccessException
                or HttpRequestException
                or JsonException
                or System.Xml.XmlException
                or OperationCanceledException
                or InvalidOperationException
                or KeyNotFoundException
    )
{
    await Console.Error.WriteLineAsync(
        $"Analysis stopped ({error.GetType().Name}); inspect the private logs and prerequisites."
    );

    return 1;
}
