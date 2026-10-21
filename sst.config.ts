/// <reference path="./.sst/platform/config.d.ts" />

/**
 * Runs the analyzer image on Lambda behind a Function URL. The image is the same
 * one docker-compose runs: the Lambda Web Adapter baked into it turns each
 * invocation into an HTTP request to the FastAPI server, so nothing here is
 * Lambda-specific code.
 *
 * Secrets, per stage (`sst secret set <Name> <value> --stage production`):
 * - AnalyzerToken: bearer token callers must send (the backend's ANALYZER_TOKEN)
 * - MbContact: URL or email for the MusicBrainz User-Agent
 * - AcoustidApiKey, DiscogsToken: optional; empty skips that source
 */
export default $config({
  app(input) {
    return {
      name: "claqradio-analyzer",
      removal: input?.stage === "production" ? "retain" : "remove",
      protect: ["production"].includes(input?.stage),
      home: "aws",
      providers: {
        aws: {
          // Falls back to the usual AWS SDK resolution (AWS_REGION,
          // AWS_PROFILE, ~/.aws/config) when unset.
          region: process.env.AWS_REGION as aws.Region | undefined,
        },
        "docker-build": "0.0.14",
      },
    };
  },
  async run() {
    const token = new sst.Secret("AnalyzerToken");
    const mbContact = new sst.Secret("MbContact");
    const acoustidApiKey = new sst.Secret("AcoustidApiKey", "");
    const discogsToken = new sst.Secret("DiscogsToken", "");

    const repository = new aws.ecr.Repository("AnalyzerImages", {
      // ECR names must be lowercase, which the generated "AnalyzerImages-<hash>" isn't.
      name: `${$app.name}-${$app.stage}`.toLowerCase(),
      forceDelete: true,
    });
    // Each image is a few GB, and only the one Lambda runs is needed.
    new aws.ecr.LifecyclePolicy("AnalyzerImagesExpiry", {
      repository: repository.name,
      policy: JSON.stringify({
        rules: [
          {
            rulePriority: 1,
            description: "Keep the three most recent images",
            selection: { tagStatus: "any", countType: "imageCountMoreThan", countNumber: 3 },
            action: { type: "expire" },
          },
        ],
      }),
    });

    const registry = aws.ecr.getAuthorizationTokenOutput({
      registryId: repository.registryId,
    });
    const image = new dockerbuild.Image("AnalyzerImage", {
      context: { location: $cli.paths.root },
      // essentia-tensorflow only ships x86_64 wheels.
      platforms: ["linux/amd64"],
      tags: [$interpolate`${repository.repositoryUrl}:latest`],
      push: true,
      registries: [
        {
          address: registry.proxyEndpoint,
          username: registry.userName,
          password: registry.password,
        },
      ],
    });

    const role = new aws.iam.Role("AnalyzerRole", {
      assumeRolePolicy: aws.iam.assumeRolePolicyForPrincipal({
        Service: "lambda.amazonaws.com",
      }),
    });
    new aws.iam.RolePolicyAttachment("AnalyzerLogs", {
      role: role.name,
      policyArn: aws.iam.ManagedPolicy.AWSLambdaBasicExecutionRole,
    });

    const analyzer = new aws.lambda.Function("Analyzer", {
      packageType: "Image",
      // By digest, so each new image redeploys the function. Lambda rejects the
      // combined tag@digest form that `image.ref` gives.
      imageUri: $interpolate`${repository.repositoryUrl}@${image.digest}`,
      role: role.arn,
      architectures: ["x86_64"],
      // The Essentia and bge-small models together peak around 1.6 GB, and CPU
      // scales with memory: 3 GB is about 1.7 vCPUs.
      memorySize: 3008,
      // Rails waits up to 300s for a response.
      timeout: 300,
      // One instance at a time: the MusicBrainz (1 req/s), AcoustID and Discogs
      // rate limits are enforced in-process, so parallel instances would exceed
      // them. Extra concurrent requests get a 429, which the backend retries later.
      reservedConcurrentExecutions: 1,
      // Downloaded songs, and the metadata cache, which lives as long as the
      // instance stays warm.
      ephemeralStorage: { size: 2048 },
      environment: {
        variables: {
          ANALYZER_TOKEN: token.value,
          MB_CONTACT: mbContact.value,
          ACOUSTID_API_KEY: acoustidApiKey.value,
          DISCOGS_TOKEN: discogsToken.value,
          // No process pool: Lambda has no /dev/shm.
          ANALYZE_WORKERS: "0",
          // /tmp is the only writable path.
          CACHE_DIR: "/tmp/cache",
          HOME: "/tmp",
        },
      },
    });

    const url = new aws.lambda.FunctionUrl("AnalyzerUrl", {
      functionName: analyzer.name,
      // Public URL; the analyzer itself checks the bearer token, so callers
      // need nothing AWS-specific (no SigV4 signing).
      authorizationType: "NONE",
    });
    new aws.lambda.Permission("AnalyzerPublicUrl", {
      action: "lambda:InvokeFunctionUrl",
      function: analyzer.name,
      principal: "*",
      functionUrlAuthType: "NONE",
    });
    new aws.lambda.Permission("AnalyzerInvokeViaUrl", {
      action: "lambda:InvokeFunction",
      function: analyzer.name,
      principal: "*",
      invokedViaFunctionUrl: true,
    });

    return {
      // The backend's ANALYZER_URL.
      url: url.functionUrl,
    };
  },
});
