import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;
import java.io.File;
import java.io.FileInputStream;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.Collections;

public class PersonalizedSigner {
    public static void main(String[] args) throws Exception {
        char[] pass = new String(Files.readAllBytes(Paths.get(args[1])), StandardCharsets.UTF_8).trim().toCharArray();
        KeyStore ks = KeyStore.getInstance("PKCS12");
        try (FileInputStream in = new FileInputStream(args[0])) { ks.load(in, pass); }
        PrivateKey key = (PrivateKey) ks.getKey(args[2], pass);
        X509Certificate cert = (X509Certificate) ks.getCertificate(args[2]);
        String fingerprint = java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()));
        String expected = java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Paths.get(args[5]))));
        if (!fingerprint.equals(expected)) {
            throw new IllegalStateException("Signer does not match reviewed native compatibility constant");
        }
        java.util.Arrays.fill(pass, '\0');
        ApkSigner.SignerConfig config = new ApkSigner.SignerConfig.Builder("lejian", key, Collections.singletonList(cert)).build();
        new ApkSigner.Builder(Collections.singletonList(config))
            .setInputApk(new File(args[3])).setOutputApk(new File(args[4]))
            .setV1SigningEnabled(true).setV2SigningEnabled(true).build().sign();
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[4])).build().verify();
        if (!result.isVerified() || !result.isVerifiedUsingV1Scheme() || !result.isVerifiedUsingV2Scheme()) {
            throw new IllegalStateException("APK signature verification failed");
        }
        System.out.println("verified=true v1=true v2=true");
    }
}
